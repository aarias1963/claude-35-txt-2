import streamlit as st
import anthropic
import pandas as pd
import PyPDF2
import io
import re
import uuid
import time
import traceback
import csv
from typing import Dict, List, Tuple

class ChatMessage:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content

class Exercise:
    def __init__(self, number: str, page: int, description: str, suitability: int):
        self.number = number
        self.page = page
        self.description = description
        self.suitability = suitability

def init_session_state():
    """Inicializa las variables de estado de la sesión"""
    if "current_results" not in st.session_state:
        st.session_state["current_results"] = None
    if "combined_response" not in st.session_state:
        st.session_state["combined_response"] = ""
    if "analysis_error" not in st.session_state:
        st.session_state["analysis_error"] = None
    if "has_results" not in st.session_state:
        st.session_state["has_results"] = False
    if "file_chunks" not in st.session_state:
        st.session_state["file_chunks"] = []
    if "analysis_done" not in st.session_state:
        st.session_state["analysis_done"] = False

def parse_text_with_pages(text):
    pages = {}
    current_page = None
    current_content = []
    
    lines = text.split('\n')
    
    try:
        page_pattern = r'\[Página (\d+)\]'
        
        for i, line in enumerate(lines):
            match = re.match(page_pattern, line, re.UNICODE)
            if match:
                if current_page:
                    pages[current_page] = '\n'.join(current_content).encode('utf-8').decode('utf-8')
                current_page = int(match.group(1))
                current_content = []
            elif current_page is not None:
                current_content.append(line)
        
        if current_page and current_content:
            pages[current_page] = '\n'.join(current_content).encode('utf-8').decode('utf-8')
        
        return pages
    except Exception as e:
        st.error(f"Error procesando el texto: {str(e)}")
        raise e

def parse_exercises_from_response(response: str) -> List[Exercise]:
    exercises = []
    exercise_pattern = r'Ejercicio\s+(\d+)\s*\(Página\s+(\d+)\)\s*\[Idoneidad:\s*(\d+)\]\s*:\s*((?:(?!Ejercicio\s+\d+\s*\(Página).|[\n])*)'
    
    matches = re.finditer(exercise_pattern, response, re.DOTALL | re.IGNORECASE | re.UNICODE)
    
    for match in matches:
        number = match.group(1)
        page = int(match.group(2))
        suitability = int(match.group(3))
        description = match.group(4).strip() if match.group(4) else "Sin descripción"
        exercises.append(Exercise(number, page, description.encode('utf-8').decode('utf-8'), suitability))
            
    return exercises

def chunk_pages_into_files(pages_content: Dict[int, str], pages_per_chunk: int = 25) -> List[Dict[int, str]]:
    st.write("Iniciando procesamiento...")  # Debug
    pages_list = sorted(pages_content.items())
    chunks = []
    
    for i in range(0, len(pages_list), pages_per_chunk):
        chunk = dict(pages_list[i:i + pages_per_chunk])
        chunks.append(chunk)
    
    return chunks

def save_analysis_results(all_exercises: List[Exercise], combined_response: str):
    """Guarda los resultados del análisis en el estado de la sesión"""
    try:
        if not all_exercises:
            return False

        # Crear DataFrame con el orden de columnas deseado
        df = pd.DataFrame([{
            'Página': ex.page,
            'Ejercicio': ex.number,
            'Idoneidad': ex.suitability,
            'Descripción': ex.description
        } for ex in all_exercises])
        
        # Convertir Ejercicio a numérico y ordenar
        df['Ejercicio'] = pd.to_numeric(df['Ejercicio'], errors='coerce')
        df = df.sort_values(['Idoneidad', 'Página', 'Ejercicio'], 
                          ascending=[False, True, True])
        
        # Guardar en el estado de la sesión y cache
        st.session_state['current_results'] = df
        st.session_state['combined_response'] = combined_response
        st.session_state['analysis_error'] = None
        st.session_state['has_results'] = True
        
        # Crear respaldo de los resultados
        st.session_state['backup_results'] = {
            'df': df.to_dict(),
            'response': combined_response
        }
        return True
    except Exception as e:
        st.session_state['analysis_error'] = str(e)
        return False

def display_results():
    """Muestra los resultados del análisis"""
    try:
        if 'backup_results' in st.session_state and st.session_state.get('has_results'):
            # Recuperar desde backup si es necesario
            if st.session_state.current_results is None and st.session_state.backup_results:
                st.session_state.current_results = pd.DataFrame.from_dict(st.session_state.backup_results['df'])
                st.session_state.combined_response = st.session_state.backup_results['response']

        if st.session_state.get('current_results') is not None:
            st.write("### Resultados del Análisis")
            
            with st.spinner('Cargando resultados...'):
                # Mostrar DataFrame con caché
                st.dataframe(st.session_state.current_results, use_container_width=True)
            
            # Botones de descarga
            col1, col2 = st.columns(2)
            with col1:
                csv_data = st.session_state.current_results.to_csv(index=False, encoding='utf-8-sig')
                st.download_button(
                    label="📥 Descargar CSV",
                    data=csv_data.encode('utf-8-sig'),
                    file_name="analisis_ejercicios.csv",
                    mime="text/csv"
                )
            
            with col2:
                excel_buffer = io.BytesIO()
                st.session_state.current_results.to_excel(excel_buffer, index=False, engine='openpyxl')
                excel_buffer.seek(0)
                st.download_button(
                    label="📥 Descargar Excel",
                    data=excel_buffer,
                    file_name="analisis_ejercicios.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            
            # Mostrar resultados detallados
            if st.session_state.combined_response:
                st.write("### Resultados Detallados")
                st.write(st.session_state.combined_response)
        
        elif st.session_state.analysis_error:
            st.error(f"Error en el análisis: {st.session_state.analysis_error}")
            
    except Exception as e:
        st.error(f"Error al mostrar resultados: {str(e)}")

def query_chunk(client, chunk: Dict[int, str], prompt: str, chunk_info: str) -> str:
    formatted_messages = []
    content_message = f"""Analizando {chunk_info}.

ESTÁNDAR ESPECÍFICO A BUSCAR: {prompt}

IMPORTANTE: Analiza ÚNICAMENTE ejercicios que trabajen DIRECTAMENTE el estándar especificado.

Para CADA ejercicio que SÍ trabaje específicamente el estándar, usa EXACTAMENTE este formato:
Ejercicio X (Página Y) [Idoneidad: Z]: Descripción completa del ejercicio

Donde Z es la valoración de idoneidad específica para el estándar:
5 = Trabaja directa y completamente el estándar especificado
4 = Trabaja el estándar de manera clara y efectiva
3 = Trabaja el estándar pero de forma parcial o indirecta
2 = Apenas toca el estándar especificado
1 = Relacionado muy vagamente con el estándar

EXCLUSIONES - NO incluyas ejercicios que:
- Solo mencionen tangencialmente temas relacionados
- Trabajen conceptos generales pero no el estándar específico
- Sean ejercicios de gramática, vocabulario general, o comprensión si no trabajan específicamente el estándar
- Practiquen otras habilidades aunque sean del mismo tema general

Documento a analizar:
""".encode('utf-8').decode('utf-8')
    
    for page, content in sorted(chunk.items()):
        content_message += f"[Página {page}]\n{content}\n\n".encode('utf-8').decode('utf-8')
    
    formatted_messages.append({
        "role": "user",
        "content": content_message
    })
    
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=formatted_messages,
        system="""Eres un especialista en análisis pedagógico de materiales educativos. Tu tarea es identificar ÚNICAMENTE ejercicios que trabajen específicamente el estándar educativo solicitado.

INSTRUCCIONES CRÍTICAS:
1. SOLO analiza ejercicios que trabajen DIRECTAMENTE el estándar especificado
2. SÉ MUY SELECTIVO - es mejor no incluir un ejercicio que incluir uno irrelevante
3. Para cada ejercicio relevante, usa EXACTAMENTE este formato:
   Ejercicio X (Página Y) [Idoneidad: Z]: Descripción
4. La valoración (Z) debe reflejar qué tan específicamente trabaja el estándar (1-5)
5. NO incluyas ejercicios que solo sean temáticamente relacionados
6. NO incluyas ejercicios de práctica general de vocabulario o gramática a menos que trabajen específicamente el estándar

PROCESO DE ANÁLISIS:
1. Lee el estándar específico solicitado
2. Examina cada ejercicio preguntándote: "¿Este ejercicio trabaja DIRECTAMENTE este estándar específico?"
3. Solo si la respuesta es SÍ, inclúyelo en el análisis
4. Evalúa la idoneidad basándote en qué tan específicamente aborda el estándar

EJEMPLOS:
- Si el estándar es "adjetivos de personalidad", SOLO incluye ejercicios que practiquen específicamente adjetivos como simpático, tímido, extrovertido, etc.
- NO incluyas ejercicios generales de descripción física, vocabulario de familia, o gramática de adjetivos a menos que trabajen específicamente personalidad

Recuerda: Es mejor ser conservador y específico que genérico e inclusivo.""".encode('utf-8').decode('utf-8')
    )
    
    return response.content[0].text

def main():
    init_session_state()
    st.set_page_config(
        page_title="Análisis de Ejercicios",
        page_icon="📚",
        layout="wide"
    )

    st.sidebar.title("⚙️ Configuración")
    api_key = st.sidebar.text_input("API Key de Anthropic", type="password")

    st.sidebar.markdown("### 📄 Cargar Archivo")
    uploaded_file = st.sidebar.file_uploader("Sube un archivo PDF o TXT", type=['pdf', 'txt'])

    st.sidebar.markdown("### 🗑️ Gestión")
    if st.sidebar.button("🔄 Nuevo Análisis", type="primary", use_container_width=True):
        # Limpiar todos los estados
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

    st.title("📚 Análisis de Ejercicios por Estándar")
    st.markdown("""
    Esta aplicación analiza ejercicios educativos y los clasifica según estándares específicos.
    
    **Instrucciones:**
    1. Sube un archivo TXT o PDF con el manual educativo
    2. Describe de forma MUY ESPECÍFICA el estándar educativo que quieres buscar
    3. Obtén un análisis detallado y exportable de ejercicios que trabajen ese estándar específico
    
    **Consejos para mejores resultados:**
    - Sé específico: en lugar de "vocabulario", usa "adjetivos de personalidad: simpático, tímido, extrovertido"
    - Incluye ejemplos del vocabulario o conceptos específicos que buscas
    - Especifica el tipo de habilidad: "práctica oral de", "ejercicios escritos de", etc.
    """)

    if not api_key:
        st.warning("👈 Introduce tu API Key en la barra lateral para comenzar.")
        return

    try:
        client = anthropic.Client(api_key=api_key)
        
        if uploaded_file:
            try:
                # Procesar archivo
                content = uploaded_file.getvalue().decode('utf-8')
                pages = parse_text_with_pages(content)
                if pages:
                    st.session_state["file_chunks"] = chunk_pages_into_files(pages)
                    st.success(f"Archivo cargado: {uploaded_file.name} ({len(pages)} páginas)")

            except Exception as e:
                st.error(f"Error al procesar el archivo: {str(e)}")

        # Input para el estándar con ejemplo
        st.markdown("### 🎯 Especifica el Estándar a Buscar")
        st.markdown("**Ejemplo:** *Adjetivos de personalidad: simpático/a, tímido/a, extrovertido/a, trabajador/a, inteligente, perezoso/a*")
        
        if prompt := st.chat_input("Describe de forma ESPECÍFICA el estándar educativo..."):
            try:
                if st.session_state["file_chunks"]:
                    # Mostrar el estándar analizado
                    st.info(f"**Analizando estándar:** {prompt}")
                    
                    # Iniciar análisis
                    combined_response = ""
                    all_exercises = []
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    try:
                        for i, chunk in enumerate(st.session_state["file_chunks"]):
                            chunk_start = min(chunk.keys())
                            chunk_end = max(chunk.keys())
                            chunk_info = f"páginas {chunk_start} a {chunk_end}"
                            
                            status_text.text(f"Analizando {chunk_info}...")
                            
                            if i > 0:
                                status_text.text("Esperando para continuar el análisis...")
                                time.sleep(65)
                            
                            response = query_chunk(client, chunk, prompt, chunk_info)
                            if response.strip():
                                chunk_exercises = parse_exercises_from_response(response)
                                if chunk_exercises:
                                    combined_response += f"\n\nResultados de {chunk_info}:\n{response}"
                                    all_exercises.extend(chunk_exercises)
                            
                            progress = (i + 1) / len(st.session_state["file_chunks"])
                            progress_bar.progress(progress)
                        
                        status_text.text("Análisis completado!")
                        
                        # Guardar resultados en el estado
                        if all_exercises:
                            if save_analysis_results(all_exercises, combined_response):
                                st.success(f"✅ Se encontraron {len(all_exercises)} ejercicios que trabajan específicamente el estándar solicitado")
                                # Mostrar resultados
                                display_results()
                            else:
                                st.error("Error al guardar los resultados del análisis")
                        else:
                            st.warning("❌ No se encontraron ejercicios que trabajen específicamente el estándar solicitado.")
                            st.info("💡 **Sugerencias:**\n- Verifica que el estándar esté presente en el manual\n- Intenta ser más específico o más general en la descripción\n- Revisa si usaste la terminología correcta")
                    
                    except Exception as e:
                        st.error(f"Error durante el análisis: {str(e)}")
                        st.session_state["analysis_error"] = str(e)
                    
                else:
                    st.warning("Por favor, carga un archivo antes de realizar el análisis.")

            except Exception as e:
                st.error(f"Error en el análisis: {str(e)}")
                st.write(f"Detalles del error: {traceback.format_exc()}")

    except Exception as e:
        st.error(f"Error de inicialización: {str(e)}")

if __name__ == "__main__":
    main()

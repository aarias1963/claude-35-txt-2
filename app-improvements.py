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

# Añadimos nuevas variables de estado
if "current_results" not in st.session_state:
    st.session_state.current_results = None
if "combined_response" not in st.session_state:
    st.session_state.combined_response = ""
if "analysis_error" not in st.session_state:
    st.session_state.analysis_error = None

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
        
        # Guardar en el estado de la sesión
        st.session_state.current_results = df
        st.session_state.combined_response = combined_response
        st.session_state.analysis_error = None
        return True
    except Exception as e:
        st.session_state.analysis_error = str(e)
        return False

def display_results():
    """Muestra los resultados del análisis"""
    try:
        if st.session_state.current_results is not None:
            st.write("### Resultados del Análisis")
            
            # Mostrar DataFrame
            st.dataframe(st.session_state.current_results)
            
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
IMPORTANTE: Para CADA ejercicio que encuentres, usa EXACTAMENTE este formato:
Ejercicio X (Pagina Y) [Idoneidad: Z]: Descripción completa del ejercicio

Donde Z es un valor del 1 al 5 que indica el grado de idoneidad del ejercicio con el estándar solicitado:
5 = Muy idóneo (cumple perfectamente con el estándar)
4 = Bastante idóneo (cumple bien con el estándar)
3 = Moderadamente idóneo (cumple parcialmente con el estándar)
2 = Poco idóneo (cumple mínimamente con el estándar)
1 = Muy poco idóneo (apenas cumple con el estándar)

Es OBLIGATORIO incluir la valoración de idoneidad para cada ejercicio.

Documento a analizar:
""".encode('utf-8').decode('utf-8')
    
    for page, content in sorted(chunk.items()):
        content_message += f"[Página {page}]\n{content}\n\n".encode('utf-8').decode('utf-8')
    
    formatted_messages.append({
        "role": "user",
        "content": content_message
    })
    formatted_messages.append({
        "role": "user", 
        "content": prompt.encode('utf-8').decode('utf-8')
    })
    
    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=4096,
        messages=formatted_messages,
        system="""Eres un asistente especializado en análisis de ejercicios educativos. REGLAS:

1. Para CADA ejercicio encontrado, DEBES usar EXACTAMENTE este formato:
   Ejercicio X (Página Y) [Idoneidad: Z]: Descripción
   donde Z DEBE ser un número del 1 al 5
2. Es OBLIGATORIO incluir la valoración de idoneidad [Idoneidad: Z]
3. La valoración DEBE ser un número entero entre 1 y 5
4. NO omitas la valoración en ningún ejercicio
5. Analiza SOLO ejercicios que cumplan con el estándar solicitado""".encode('utf-8').decode('utf-8')
    )
    
    return response.content[0].text

def main():
    st.set_page_config(
        page_title="Análisis de Ejercicios",
        page_icon="📚",
        layout="wide"
    )

    # Inicialización del estado
    if "file_chunks" not in st.session_state:
        st.session_state.file_chunks = []
    if 'analysis_done' not in st.session_state:
        st.session_state.analysis_done = False

    st.sidebar.title("⚙️ Configuración")
    api_key = st.sidebar.text_input("API Key de Anthropic", type="password")

    st.sidebar.markdown("### 📄 Cargar Archivo")
    uploaded_file = st.sidebar.file_uploader("Sube un archivo PDF o TXT", type=['pdf', 'txt'])

    st.sidebar.markdown("### 🗑️ Gestión")
    if st.sidebar.button("🔄 Nuevo Análisis", type="primary", use_container_width=True):
        st.session_state.analysis_done = False
        st.rerun()

    st.title("📚 Análisis de Ejercicios por Estándar")
    st.markdown("""
    Esta aplicación analiza ejercicios educativos y los clasifica según estándares específicos.
    1. Sube un archivo TXT o PDF
    2. Describe el estándar educativo que quieres buscar
    3. Obtén un análisis detallado y exportable
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
                    st.session_state.file_chunks = chunk_pages_into_files(pages)
                    st.success(f"Archivo cargado: {uploaded_file.name}")

            except Exception as e:
                st.error(f"Error al procesar el archivo: {str(e)}")

        # Input para el estándar
        if prompt := st.chat_input("Describe el estándar educativo a buscar..."):
            try:
                if st.session_state.file_chunks:
                    # Iniciar análisis
                    combined_response = ""
                    all_exercises = []
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    try:
                        for i, chunk in enumerate(st.session_state.file_chunks):
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
                            
                            progress = (i + 1) / len(st.session_state.file_chunks)
                            progress_bar.progress(progress)
                        
                        status_text.text("Análisis completado!")
                        
                        # Guardar resultados en el estado
                        if all_exercises:
                            if save_analysis_results(all_exercises, combined_response):
                                # Mostrar resultados
                                display_results()
                            else:
                                st.error("Error al guardar los resultados del análisis")
                        else:
                            st.write("No se encontraron ejercicios que cumplan con el estándar especificado.")
                    
                    except Exception as e:
                        st.error(f"Error durante el análisis: {str(e)}")
                        st.session_state.analysis_error = str(e)
                    
                else:
                    st.warning("Por favor, carga un archivo antes de realizar el análisis.")

            except Exception as e:
                st.error(f"Error en el análisis: {str(e)}")
                st.write(f"Detalles del error: {traceback.format_exc()}")

    except Exception as e:
        st.error(f"Error de inicialización: {str(e)}")

if __name__ == "__main__":
    main()

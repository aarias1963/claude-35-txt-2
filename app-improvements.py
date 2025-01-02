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

# Añadimos nuevas variables de estado
if "current_results" not in st.session_state:
    st.session_state.current_results = None
if "combined_response" not in st.session_state:
    st.session_state.combined_response = ""
if "analysis_error" not in st.session_state:
    st.session_state.analysis_error = None

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

def main():
    # ... (código anterior igual hasta el análisis)

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

# ... (resto del código igual)

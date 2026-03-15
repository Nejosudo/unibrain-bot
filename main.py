import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from supabase import create_client
import google.generativeai as genai
from flask import Flask
import threading

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

threading.Thread(target=run).start()

# Configurar Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-2.5-flash')

# Cargar llaves
load_dotenv()
bot = Bot(token=os.getenv("TELEGRAM_TOKEN"))
dp = Dispatcher()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# Tu Horario para la "Memoria"
HORARIO_CONTEXTO = {
    "u1_nombre": "UNIPAZ",
    "u1_horario": {
        "Lunes": "Formulación y evaluacion de proyecos (7AM-10AM)",
        "Martes": "Informatica Forense (7AM-11AM)",
        "Miércoles": "Seminario de investigacion (8AM-11AM)",
        "Jueves": "Desarrollo de aplicaciones moviles (7AM-11AM)",
        "Viernes": "Calidad de software (7AM-10AM), Seguridad de la información (10AM-13PM)"
    },
    "u2_nombre": "Praxis-Inglés",
    "u2_horario": "Lunes a Jueves, 6:00 PM - 9:00 PM (Niveles de Inglés)"
}

ADMIN_ID = os.getenv("ADMIN_ID")

async def es_autorizado(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("🚫 Acceso denegado. Este bot es privado.")
        return False
    return True

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if not await es_autorizado(message): return
    # Guardar tu perfil la primera vez
    user_id = message.from_user.id
    supabase.table("user_profile").upsert({
        "user_id": user_id,
        "d_schedule": {"info": HORARIO_CONTEXTO["u1_horario"], "nombre": HORARIO_CONTEXTO["u1_nombre"]},
        "n_schedule": {"info": HORARIO_CONTEXTO["u2_horario"], "nombre": HORARIO_CONTEXTO["u2_nombre"]}
    }).execute()
    
    await message.answer(f"¡Hola! Soy tu asistente de UNIPAZ y Praxis. Todo lo que me escribas lo anotaré para tu resumen semanal.")

@dp.message(Command("resumen"))
async def cmd_resumen(message: types.Message):
    #print("¡Comando /resumen detectado!")
    if not await es_autorizado(message): return
    user_id = message.from_user.id
    await message.answer("🔄 Consultando a mi cerebro de IA... un momento.")

    try:
        # 1. Obtener notas no procesadas
        res_notas = supabase.table("activities").select("*")\
            .eq("user_id", user_id)\
            .eq("is_processed", False).execute()
        
        if not res_notas.data:
            await message.answer("No tengo nada nuevo anotado. ¡Tu agenda está limpia! ✨")
            return

        notas = [fila['content'] for fila in res_notas.data]
        
        # 2. Obtener el perfil
        res_perfil = supabase.table("user_profile").select("*").eq("user_id", user_id).single().execute()
        perfil = res_perfil.data

        # 3. Prompt (Asegúrate de que las llaves d_schedule coincidan con tu tabla)
        prompt_sistema = f"""
        Actúa como un asistente académico experto y amigo. Usuario:
        - Mañana: {perfil['d_schedule']['nombre']} ({perfil['d_schedule']['info']})
        - Noche: {perfil['n_schedule']['nombre']} ({perfil['n_schedule']['info']})
        
        Notas de la semana:
        {chr(10).join(notas)}
        Usa ÚNICAMENTE negritas con doble asterisco (ej: **Texto**), no uses tablas, ni otros formatos complejos.
        Asegúrate de cerrar siempre todos los asteriscos. No uses simbolos extraños.
        TAREA:
        1. Clasifica en: TAREAS PENDIENTES, EXÁMENES o NOTAS.
        2. Organiza por prioridad.
        3. Usa emojis y sé breve.
        
        INSTRUCCIONES DE FORMATO (Sigue esto estrictamente):
        
        1. **RESUMEN DE LA SEMANA** 📅
           (Haz un breve párrafo motivador de 2 líneas máximo).
        
        2. **PENDIENTES POR PRIORIDAD** 🚨
           - [MATERIA] | Tarea | Fecha límite (si existe) | Prioridad (Alta/Media/Baja)
        
        3. **NOTAS DE CLASE / RECUERDOS** 💡
           - (Agrupa aquí información que no sea una tarea, como fechas de exámenes o comentarios).
        
        4. **ESTRATEGIA DE ESTUDIO RECOMENDADA** 🧠
           - (Basado en el horario de UNIPAZ y Praxis, dile exactamente en qué horas libres debería atacar los pendientes de prioridad Alta).
        
        Usa negritas para resaltar materias. Mantén un tono ejecutivo pero cercano. No inventes tareas que no estén en las notas.

        """

        # 4. Llamada a Gemini
        response = model.generate_content(prompt_sistema)
        texto_ia = response.text
        # 5. ENVIAR RESUMEN PRIMERO
        # Usamos un try-except interno por si el Markdown de Gemini rompe Telegram
        try:
            await message.answer(texto_ia, parse_mode="Markdown")
        except:
            await message.answer(texto_ia) # Si falla el markdown, envía texto plano

        # 6. ARCHIVAR después de mostrarlo
        ids_procesados = [fila['id'] for fila in res_notas.data]
        if ids_procesados:
            #print(f"Archivando IDs: {ids_procesados}")
            resultado = supabase.table("activities").update({"is_processed": True})\
                .in_("id", ids_procesados).execute()

        #print(f"Filas actualizadas: {len(resultado.data)}")
        
        await message.answer("🧹 Notas archivadas. ¡Nueva semana comenzada!")

    except Exception as e:
        #print(f"Error en resumen: {e}")
        await message.answer("Uf, me dio un pequeño calambre cerebral. Inténtalo de nuevo.")

@dp.message(Command("buscar"))
async def cmd_buscar(message: types.Message):
    if not await es_autorizado(message): return
    
    # Extraer la palabra clave después del comando /buscar
    query = message.text.replace("/buscar", "").strip()
    
    if not query:
        await message.answer("🧐 ¿Qué quieres buscar? Ejemplo: `/buscar examen`")
        return

    await message.answer(f"🔍 Buscando '{query}' en tu cerebro digital...")

    try:
        # Buscamos en la columna 'content' usando ilike (ignora mayúsculas/minúsculas)
        res = supabase.table("activities")\
            .select("*")\
            .eq("user_id", message.from_user.id)\
            .ilike("content", f"%{query}%")\
            .order("created_at", desc=True)\
            .limit(5).execute()

        if not res.data:
            await message.answer(f"No encontré nada relacionado con '{query}'. 🤷‍♂️")
            return

        respuesta = f"📍 **Resultados para '{query}':**\n\n"
        for i, nota in enumerate(res.data, 1):
            fecha = nota['created_at'][:10] # Tomamos solo la fecha AAAA-MM-DD
            respuesta += f"{i}. [{fecha}] {nota['content']}\n"

        await message.answer(respuesta, parse_mode="Markdown")

    except Exception as e:
        print(f"Error en búsqueda: {e}")
        await message.answer("Error al buscar en la base de datos.")

@dp.message()
async def handle_all_messages(message: types.Message):
    if not await es_autorizado(message): return
    # Evitar procesar comandos como texto de tarea
    if message.text.startswith('/'):
        return

    # Guardar en la tabla 'activities' (Opción B: Texto Crudo)
    try:
        supabase.table("activities").insert({
            "user_id": message.from_user.id,
            "content": message.text,
            "category": "pending" # Luego Gemini lo clasificará
        }).execute()
        await message.reply("📝 Anotado.")
    except Exception as e:
        await message.reply("Error al guardar en base de datos.")
        #print(e)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
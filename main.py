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

threading.Thread(target=run, daemon=True).start()

# Cargar variables de entorno
load_dotenv()

# Configurar Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-2.5-flash')

# Inicializar clientes
bot = Bot(token=os.getenv("TELEGRAM_TOKEN"))
dp = Dispatcher()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# Horario para la "Memoria"
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
    # Convertimos ambos a string para evitar errores de tipo de dato
    if str(message.from_user.id) != str(ADMIN_ID):
        print(f"DEBUG: Bloqueado usuario {message.from_user.id}")
        await message.answer("🚫 Acceso denegado. Este bot es privado.")
        return False
    return True

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if not await es_autorizado(message): return
    # Guardar perfil la primera vez
    user_id = message.from_user.id
    supabase.table("user_profile").upsert({
        "user_id": user_id,
        "d_schedule": {"info": HORARIO_CONTEXTO["u1_horario"], "nombre": HORARIO_CONTEXTO["u1_nombre"]},
        "n_schedule": {"info": HORARIO_CONTEXTO["u2_horario"], "nombre": HORARIO_CONTEXTO["u2_nombre"]}
    }).execute()
    
    await message.answer(f"¡Hola! Soy tu asistente de UNIPAZ y Praxis. Todo lo que me escribas lo anotaré para tu resumen semanal.")

@dp.message(Command("day"))
async def cmd_day(message: types.Message):
    if not await es_autorizado(message): return
    
    # Extraer tarea eliminando el comando inicial
    partes = message.text.split(maxsplit=1)
    tarea = partes[1].strip() if len(partes) > 1 else ""
    if not tarea:
        await message.answer("📝 Escribe la tarea después del comando. Ej: `/day Estudiar Java`.")
        return

    supabase.table("daily_tasks").insert({
        "user_id": message.from_user.id,
        "task_description": tarea
    }).execute()
    
    await message.answer(f"✅ Añadido: {tarea}")

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
        
        if not perfil:
            await message.answer("⚠️ No he encontrado tu perfil. Por favor, usa /start primero para configurarlo.")
            return

        # 3. Prompt
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

user_tasks_cache = {}

@dp.message(Command("day_task")) # Telegram no permite guiones en comandos, usa guion bajo
async def cmd_day_task(message: types.Message):
    if not await es_autorizado(message): return

    res = supabase.table("daily_tasks")\
        .select("*")\
        .eq("user_id", message.from_user.id)\
        .eq("is_archived", False)\
        .order("created_at", desc=False).execute()

    if not res.data:
        await message.answer("No tienes tareas pendientes para hoy. ✨")
        return

    texto = "📋 **Tus tareas de hoy:**\n"
    cache = {}
    
    for i, t in enumerate(res.data, 1):
        status = "✅" if t['is_completed'] else "⏳"
        texto += f"{i}. {status} {t['task_description']}\n"
        cache[i] = t['id'] # Guardamos la relación Número <-> ID real
    
    user_tasks_cache[message.from_user.id] = cache
    texto += "\n💡 *Para completar una:* escribe `/hecho` seguido del número (ej: `/hecho 1`)."
    try:
        await message.answer(texto, parse_mode="Markdown")
    except:
        await message.answer(texto)

# Handler para detectar cuando envías solo un número
@dp.message(lambda message: message.text.isdigit())
async def marcar_tarea(message: types.Message):
    if not await es_autorizado(message): return
    
    uid = message.from_user.id
    num = int(message.text)

    # Verificamos si el usuario tiene una lista activa en el caché
    if uid in user_tasks_cache and num in user_tasks_cache[uid]:
        task_id = user_tasks_cache[uid][num]
        
        # 1. Actualizar en Supabase
        supabase.table("daily_tasks").update({"is_completed": True})\
            .eq("id", task_id).execute()
        
        # 2. Opcional: Obtener el nombre de la tarea para una respuesta más bonita
        # (Podrías guardarlo también en el caché para no volver a consultar)
        
        await message.answer(f"✅ ¡Excelente! Tarea {num} completada.")
        
        # 3. Limpiar ese número específico del caché para que no se repita por error
        del user_tasks_cache[uid][num]
    else:
        # SI NO ESTÁ EN EL CACHÉ: Es una nota normal que casualmente es un número.
        # Llamamos a tu función handle_all_messages o dejamos que siga su flujo.
        await handle_all_messages(message)

@dp.message(Command("day_r"))
async def cmd_day_r(message: types.Message):
    if not await es_autorizado(message): return

    # 1. Obtener tareas del día
    res = supabase.table("daily_tasks")\
        .select("*")\
        .eq("user_id", message.from_user.id)\
        .eq("is_archived", False).execute()

    if not res.data:
        await message.answer("No hay actividades para resumir hoy.")
        return

    # 2. Preparar datos para Gemini
    completadas = [t['task_description'] for t in res.data if t['is_completed']]
    pendientes = [t['task_description'] for t in res.data if not t['is_completed']]

    prompt = f"""
    Eres un coach de productividad. Analiza mi día:
    Tareas completadas: {completadas}
    Tareas pendientes: {pendientes}
    
    Dame un resumen corto (máximo 4 líneas) sobre mi desempeño hoy, 
    una recomendación para mañana y felicítame si hice más del 50%.
    """
    
    response = model.generate_content(prompt)
    try:
        await message.answer(f"🌙 **Resumen del Día:**\n\n{response.text}", parse_mode="Markdown")
    except:
        await message.answer(f"🌙 Resumen del Día:\n\n{response.text}")

    # 3. Resetear el día (Archivar todo)
    supabase.table("daily_tasks").update({"is_archived": True})\
        .eq("user_id", message.from_user.id)\
        .eq("is_archived", False).execute()
    
    await message.answer("🧹 Día finalizado. ¡Nos vemos mañana!")

@dp.message(Command("hecho"))
async def cmd_hecho(message: types.Message):
    if not await es_autorizado(message): return
    
    uid = message.from_user.id
    # Extraer el número después de /hecho
    partes = message.text.split()
    
    if len(partes) < 2 or not partes[1].isdigit():
        await message.answer("⚠️ Indica el número de la tarea. Ej: `/hecho 1`")
        return

    num = int(partes[1])

    # Revisar si el número está en nuestra "memoria temporal"
    if uid in user_tasks_cache and num in user_tasks_cache[uid]:
        task_id = user_tasks_cache[uid][num]
        
        # Actualizar en Supabase
        try:
            supabase.table("daily_tasks").update({"is_completed": True})\
                .eq("id", task_id).execute()
            
            await message.answer(f"✅ ¡Confirmado! La actividad {num} ya fue realizada.")
            
            # Quitamos esa tarea del caché para que no se marque dos veces
            del user_tasks_cache[uid][num]
        except Exception as e:
            print(f"Error al marcar tarea: {e}")
            await message.answer("Error al conectar con la base de datos.")
    else:
        await message.answer(f"❌ No encontré la tarea {num}. Prueba lanzando `/day_task` primero para ver la lista actual.")

@dp.message(Command("buscar"))
async def cmd_buscar(message: types.Message):
    if not await es_autorizado(message): return
    
    # Extraer la palabra clave después del comando /buscar
    # Extraer búsqueda eliminando el comando inicial
    partes = message.text.split(maxsplit=1)
    query = partes[1].strip() if len(partes) > 1 else ""
    
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
            fecha = nota['created_at'][:10] 
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

    # Guardar en la tabla 'activities'
    try:
        supabase.table("activities").insert({
            "user_id": message.from_user.id,
            "content": message.text,
            "category": "pending" 
        }).execute()
        await message.reply("📝 Anotado.")
    except Exception as e:
        await message.reply("Error al guardar en base de datos.")
        #print(e)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

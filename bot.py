import telebot
import requests
import os
import re
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WORKING_MODEL = "models/gemini-3.8-flash"

bot = telebot.TeleBot(TELEGRAM_TOKEN)
WORKSPACE_DIR = "/workspaces/NOVA-DRAGON"
RESUME_FILE = os.path.join(WORKSPACE_DIR, "RESUME.md")

chat_history = []
AGENT_PERSONA = """أنت مهندس ألعاب مستقل (NOVA AI Agent).
قاعدة صارمة: عندما يطلب منك المستخدم برمجة جزء من اللعبة، يجب أن تنشئ/تحدث الملف.
ابدأ رسالتك بـ: [SAVE: مسار/اسم_الملف.الامتداد] ثم ضع الكود داخل مربع."""

def ask_gemini(prompt):
    global chat_history
    memory = ""
    if os.path.exists(RESUME_FILE):
        with open(RESUME_FILE, "r", encoding="utf-8") as f: memory = f.read()
        
    chat_history.append({"role": "user", "parts": [{"text": prompt}]})
    if len(chat_history) > 20: chat_history = chat_history[-20:]

    url = f"https://generativelanguage.googleapis.com/v1beta/{WORKING_MODEL}:generateContent?key={GEMINI_API_KEY}"
    data = {"system_instruction": {"parts": [{"text": AGENT_PERSONA + f"\nالذاكرة: {memory}"}]}, "contents": chat_history}
    
    try:
        res = requests.post(url, headers={'Content-Type': 'application/json'}, json=data, timeout=60)
        if res.status_code == 200:
            answer = res.json()['candidates'][0]['content']['parts'][0]['text']
            chat_history.append({"role": "model", "parts": [{"text": answer}]})
            return answer
        return f"خطأ: {res.text}"
    except Exception as e:
        return str(e)

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    bot.send_chat_action(message.chat.id, 'typing')
    answer = ask_gemini(message.text)
    
    save_match = re.search(r'\[SAVE:\s*(.+?)\]', answer)
    if save_match:
        file_path = save_match.group(1).strip()
        code_match = re.search(r'```[a-zA-Z]*\n(.*?)```', answer, re.DOTALL)
        content = code_match.group(1).strip() if code_match else answer.replace(save_match.group(0), "").strip()
        full_path = os.path.join(WORKSPACE_DIR, file_path)
        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f: f.write(content)
            
            # ميزة المزامنة التلقائية مع جيت هاب
            os.system(f'git add . && git commit -m "🤖 تحديث تلقائي من NOVA Agent: تم تعديل {file_path}" && git push origin main')
            
            bot.reply_to(message, f"✅ تم الحفظ والمزامنة بنجاح في:\n`{file_path}`", parse_mode="Markdown")
            return
        except Exception as e:
            bot.reply_to(message, f"❌ خطأ في الحفظ: {e}")
            return
            
    bot.reply_to(message, answer)

print("✅ NOVA Agent is ONLINE with Auto-Sync!")
bot.infinity_polling()

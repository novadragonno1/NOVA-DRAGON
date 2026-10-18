import telebot
import requests

TELEGRAM_TOKEN = "8687704033:AAESPYO4oXlBxq0us2XUrVILHFGwE-05A7g"
GEMINI_API_KEY = "AQ.Ab8RN6LggEHvKGpf6E_A7o9ETrhoUM5pOiu-YmifPLaQ49CYPA"
WORKING_MODEL = "models/gemini-3.8-flash"

bot = telebot.TeleBot(TELEGRAM_TOKEN)

def ask_gemini(prompt):
    print(f"-> جاري إرسال الطلب إلى جوجل...")
    url = f"https://generativelanguage.googleapis.com/v1beta/{WORKING_MODEL}:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    data = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        # إضافة مهلة 15 ثانية لمنع تجمّد البوت
        response = requests.post(url, headers=headers, json=data, timeout=15)
        print(f"<- استلمنا رد من جوجل. الرمز: {response.status_code}")
        
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        else:
            print(f"! خطأ جوجل: {response.text}")
            return f"خطأ من جوجل: السيرفر رفض الطلب. راجع الطرفية."
            
    except requests.exceptions.Timeout:
        print("! انتهى وقت الاتصال (Timeout)")
        return "عذراً، سيرفر جوجل لم يرد خلال 15 ثانية (Timeout)."
    except Exception as e:
        print(f"! فشل الاتصال: {e}")
        return f"فشل الاتصال بالإنترنت: {e}"

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "تم إعادة التشغيل بنظام التتبع والمهلة الزمنية! أنا جاهز.")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    print(f"\n--- رسالة جديدة مستلمة من تيليجرام: {message.text} ---")
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        answer = ask_gemini(message.text)
        bot.reply_to(message, answer)
        print("--- تم إرسال الرد بنجاح إلى تيليجرام ---")
    except Exception as e:
        print(f"! خطأ داخلي في البوت: {e}")
        bot.reply_to(message, "حدث خطأ داخلي، راجع الطرفية.")

print(f"Game Agent is running with {WORKING_MODEL} and Tracking System...")
bot.infinity_polling()

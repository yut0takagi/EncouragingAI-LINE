import os
from flask import Flask, request, abort
from dotenv import load_dotenv
import openai
import base64
import datetime
from firebase_admin import firestore

from linebot.v3.messaging import MessagingApi, Configuration, ApiClient, TextMessage
from linebot.v3.webhook import WebhookHandler, MessageEvent
from linebot.v3.models import ReplyMessageRequest
from linebot.v3.exceptions import InvalidSignatureError
from dotenv import load_dotenv
import os

# .env読み込み
load_dotenv()
app = Flask(__name__)

# 各種APIキー
configuration = Configuration(access_token=os.getenv("LINE_ACCESS_TOKEN"))
api_client = ApiClient(configuration)
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))
openai.api_key = os.getenv("OPENAI_API_KEY")

##################################################################################################

def restore_firebase_config():
    config_b64 = os.getenv("FIREBASE_CONFIG_B64")
    if config_b64:
        decoded = base64.b64decode(config_b64)
        with open("firebase_config.json", "wb") as f:
            f.write(decoded)

restore_firebase_config()

# その後 firebase_admin で初期化
import firebase_admin
from firebase_admin import credentials

cred = credentials.Certificate("firebase_config.json")
firebase_admin.initialize_app(cred)


db = firestore.client()

def save_memory(user_id, question, answer):
    now = datetime.datetime.now(datetime.timezone.utc)
    doc_ref = db.collection("chat_memory").document(user_id)
    doc_ref.collection("messages").add({
        "timestamp": now,
        "question": question,
        "answer": answer
    })

def load_recent_memory(user_id, limit=3):
    doc_ref = db.collection("chat_memory").document(user_id)
    messages_ref = doc_ref.collection("messages").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit)
    docs = messages_ref.stream()
    history = [(d.get("question"), d.get("answer")) for d in docs]
    return list(reversed(history))  # 古い順に

##################################################################################################


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_msg = event.message.text

    # 🔁 過去の履歴を取得
    history = load_recent_memory(user_id)
    chat_history = []
    for q, a in history:
        chat_history.append({"role": "user", "content": q})
        chat_history.append({"role": "assistant", "content": a})

    chat_history.append({"role": "user", "content": user_msg})

    # 🧠 OpenAIで共感応答を生成
    system_prompt = "あなたは感情に寄り添う優しいカウンセラーです。ユーザーの話に共感し、安心させるような返答をしてください。"

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": system_prompt},
            *chat_history
        ]
    )

    reply_text = response.choices[0].message.content.strip()

    # 💬 LINEへ応答（v3スタイル）
    configuration = Configuration(access_token=os.getenv("LINE_ACCESS_TOKEN"))
    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )

    # 📝 Firestoreに保存
    save_memory(user_id, user_msg, reply_text)

if __name__ == "__main__":
    app.run()
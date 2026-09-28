import telebot
import subprocess
import os

# Initialize the bot with your token
BOT_TOKEN = "8906956328:AAFIKMNrIsvnO5P-0_aDlRT-RfhD56y808g"
bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Welcome! Send me a command like:\n`/run test.py`\nAnd I will execute it for you.", parse_mode="Markdown")

@bot.message_handler(commands=['run'])
def run_script(message):
    # Extract the file name from the message
    try:
        file_name = message.text.split(' ', 1)[1]
    except IndexError:
        bot.reply_to(message, "⚠️ Please provide a file name. Example: `/run test.py`", parse_mode="Markdown")
        return

    # Check if the file exists
    if not os.path.exists(file_name):
        bot.reply_to(message, f"❌ Error: File '{file_name}' not found.")
        return

    bot.reply_to(message, f"⏳ Running {file_name}...")

    try:
        # Create a copy of the current environment variables
        env = os.environ.copy()
        # Force Python to use UTF-8 encoding so emojis don't crash it
        env["PYTHONIOENCODING"] = "utf-8"
        
        # Execute the Python script with UTF-8 flags enabled
        result = subprocess.run(
            ['python', '-X', 'utf8', file_name], 
            capture_output=True, 
            text=True, 
            timeout=3600,
            env=env,
            encoding='utf-8'  # Decode the output as UTF-8
        )

        # Prepare the output
        output = result.stdout
        if result.stderr:
            output += f"\n❌ Errors:\n{result.stderr}"

        if not output:
            output = "✅ Script executed successfully (No output)."

        # Truncate if longer than 4000 characters
        if len(output) > 4000:
            output = output[:4000] + "\n... [Output truncated]"

        # Send the result back to Telegram
        bot.reply_to(message, f"```\n{output}\n```", parse_mode="Markdown")

    except subprocess.TimeoutExpired:
        bot.reply_to(message, "⏰ Error: The script took too long to run (over 30 seconds).")
    except Exception as e:
        bot.reply_to(message, f"❌ Unexpected error: {str(e)}")
# Start the bot
print("Bot is running... Open Telegram and send /run test.py")
bot.polling()
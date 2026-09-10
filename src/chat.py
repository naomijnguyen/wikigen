import anthropic
import os
from datetime import datetime

# Set ANTHROPIC_API_KEY in your environment before running
# export ANTHROPIC_API_KEY="sk-ant-..."

# Create a folder for saving chats if it doesn't exist
os.makedirs("chat_history", exist_ok=True)

# Initialize the client
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

print("🤖 Claude Chat (type 'quit' to exit)")
print("=" * 50)

conversation_history = []

while True:
    user_input = input("\nYou: ")
    
    if user_input.lower() in ['quit', 'exit', 'bye']:
        print("👋 Goodbye!")
        break
    
    conversation_history.append({
        "role": "user",
        "content": user_input
    })
    
    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=8096,
        messages=conversation_history
    )
    
    assistant_message = response.content[0].text
    
    conversation_history.append({
        "role": "assistant",
        "content": assistant_message
    })
    
    print(f"\nClaude: {assistant_message}")
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"chat_history/conversation_{timestamp}.txt"
    
    with open(filename, "w") as f:
        for msg in conversation_history:
            f.write(f"{msg['role'].upper()}: {msg['content']}\n\n")

print("\n💾 Chat saved!")
"""
Monitor an OpenHands conversation status
"""
import asyncio
import os
import sys
from dotenv import load_dotenv
from openhands_client import OpenHandsClient

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()

async def monitor_conversation(conversation_id: str, max_checks: int = 20):
    api_key = os.getenv("OPENHANDS_API_KEY")
    base_url = os.getenv("OPENHANDS_BASE_URL", "https://app.all-hands.dev/api")
    
    client = OpenHandsClient(api_key=api_key, base_url=base_url)
    
    print(f"Monitoring conversation: {conversation_id}")
    print(f"View at: https://app.all-hands.dev/conversations/{conversation_id}")
    print("-" * 60)
    
    try:
        for i in range(max_checks):
            try:
                status_data = await client.get_conversation_status(conversation_id)
                status = status_data.get('status', 'unknown')
                
                print(f"[Check {i+1}/{max_checks}] Status: {status}")
                
                # Print any additional useful info
                if 'conversation_status' in status_data:
                    print(f"  Conversation Status: {status_data['conversation_status']}")
                if 'message' in status_data and status_data['message']:
                    print(f"  Message: {status_data['message']}")
                if 'error' in status_data:
                    print(f"  Error: {status_data['error']}")
                
                # Check if conversation is in a final state
                if status in ['COMPLETED', 'FINISHED', 'STOPPED', 'ERROR', 'FAILED']:
                    print(f"\n[FINAL] Conversation reached final state: {status}")
                    print(f"Full response: {status_data}")
                    break
                
                # Wait before next check
                await asyncio.sleep(5)
                
            except Exception as e:
                print(f"[ERROR] Failed to get status: {e}")
                break
        else:
            print(f"\n[INFO] Reached max checks ({max_checks}). Conversation may still be running.")
            print("Check the web interface for updates.")
    
    finally:
        await client.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python monitor_conversation.py <conversation_id>")
        print("\nExample: python monitor_conversation.py 2a89b66d64a341269f0787b6270e95e8")
        sys.exit(1)
    
    conv_id = sys.argv[1]
    asyncio.run(monitor_conversation(conv_id))

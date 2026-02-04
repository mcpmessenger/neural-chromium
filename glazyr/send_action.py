import sys

def main():
    if len(sys.argv) < 2:
        print("Usage: python send_action.py \"your command here\"")
        print("Example: python send_action.py \"click search box\"")
        return

    command = " ".join(sys.argv[1:])
    
    # Write to file monitored by nexus_agent.py
    with open("manual_command.txt", "w") as f:
        f.write(command)
        
    print(f"✅ Command Sent: \"{command}\"")
    print("Agent should pick it up within 10ms.")

if __name__ == "__main__":
    main()

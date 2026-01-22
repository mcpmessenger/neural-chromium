import ollama
try:
    print("Checking Ollama connection...")
    models = ollama.list()
    print("Ollama is reachable!")
    print("Available models:")
    found = False
    for m in models['models']:
        print(f" - {m['name']}")
        if 'qwen2.5-vl' in m['name']:
            found = True
    
    if found:
        print("\nSUCCESS: qwen2.5-vl is available.")
    else:
        print("\nWARNING: qwen2.5-vl not found in model list. You may need to run 'ollama run qwen2.5-vl' once.")
        
except Exception as e:
    print(f"\nERROR: Could not connect to Ollama. Make sure the Ollama app is running.\nError: {e}")

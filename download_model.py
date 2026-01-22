import ollama
import sys

model_name = "llama3.2-vision"

print(f"Downloading {model_name}... This may take a while.")
try:
    with open("model_download.log", "w") as scan_log:
        for progress in ollama.pull(model_name, stream=True):
            if 'completed' in progress and 'total' in progress:
                percent = (progress['completed'] / progress['total']) * 100
                msg = f"Downloading: {percent:.1f}%\n"
                sys.stdout.write(f"\rDownloading: {percent:.1f}%")
                sys.stdout.flush()
                scan_log.write(msg)
            elif 'status' in progress:
                 msg = f"Status: {progress['status']}\n"
                 sys.stdout.write(f"\rStatus: {progress['status']}   ")
                 sys.stdout.flush()
                 scan_log.write(msg)
                 
    print(f"\nSuccessfully downloaded {model_name}!")
    with open("model_download.log", "a") as f: f.write("\nDONE\n")
except Exception as e:
    print(f"\nFailed to download model: {e}")
    with open("model_download.log", "a") as f: f.write(f"\nERROR: {e}\n")

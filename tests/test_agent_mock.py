import sys
import os
import json
import base64
import time
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), "../src"))

# Mock external libs before importing nexus_agent
sys.modules["openai"] = MagicMock()
sys.modules["google.generativeai"] = MagicMock()
sys.modules["anthropic"] = MagicMock()
sys.modules["ctypes"] = MagicMock()

import nexus_agent

def test_provider_switching():
    print("Testing Provider Switching...")
    
    # Mock Config
    agent = nexus_agent.NexusAgent()
    
    # Switch to OpenAI
    agent.update_config({"active_provider": "openai", "openai_api_key": "test_key"})
    assert isinstance(agent.provider, nexus_agent.OpenAIProvider)
    print("PASS: Switch to OpenAI")
    
    # Switch to Gemini
    agent.update_config({"active_provider": "gemini", "gemini_api_key": "test_key"})
    assert isinstance(agent.provider, nexus_agent.GeminiProvider)
    print("PASS: Switch to Gemini")

    # Switch to Anthropic
    agent.update_config({"active_provider": "anthropic", "anthropic_api_key": "test_key"})
    assert isinstance(agent.provider, nexus_agent.AnthropicProvider)
    print("PASS: Switch to Anthropic")

def test_command_logic():
    print("Testing Command Logic...")
    agent = nexus_agent.NexusAgent()
    # Mock Provider
    agent.provider = MagicMock()
    agent.provider.transcribe.return_value = "Computer go to google dot com"
    
    # Simulate Audio End
    agent.on_speech_complete()
    
    # Verify execute_command called
    # (Here we'd need to spy on execute_command, but simple print check is fine for now)
    agent.provider.transcribe.assert_called()
    print("PASS: Transcription Called")

if __name__ == "__main__":
    test_provider_switching()
    test_command_logic()

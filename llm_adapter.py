# llm_adapter.py
import os, json
def call_llm(prompt, max_tokens=512, temperature=0.7):
    # Try local llama.cpp/llama-cpp-python
    try:
        from llama_cpp import Llama
        model_path = os.environ.get("LLAMA_MODEL_PATH")
        if model_path:
            llm = Llama(model_path=model_path)
            resp = llm.create(prompt=prompt, max_tokens=max_tokens, temperature=temperature)
            # response shape may vary; attempt common keys
            if isinstance(resp, dict):
                if "choices" in resp and len(resp["choices"])>0:
                    return resp["choices"][0].get("text","")
                return str(resp)
            return str(resp)
    except Exception as e:
        # silently fallback
        pass
    # Fallback: OpenAI (if key present)
    try:
        import openai
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_KEY")
        if key:
            openai.api_key = key
            resp = openai.Completion.create(model="gpt-4o-mini", prompt=prompt, max_tokens=max_tokens, temperature=temperature)
            return resp["choices"][0]["text"]
    except Exception:
        pass
    return "LLM_UNAVAILABLE"

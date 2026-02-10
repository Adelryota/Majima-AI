import os
import sys
import re
from openai import OpenAI

from dotenv import load_dotenv
load_dotenv()

# --- Configuration ---
# 1. LOAD API KEY FROM ENVIRONMENT
# WARNING: This is insecure. Do not share this file.
API_KEY = os.environ.get("OPENROUTER_API_KEY") 

# 2. SET THE MODEL NAME
# Using Google Gemini 2.5 Flash Lite via OpenRouter
# Context Window: >1 Million tokens (Excellent for full lectures)
MODEL_NAME = "google/gemini-2.5-flash-lite"

# --- 3. CONFIGURE THE OPENROUTER CLIENT ---
def get_openai_client():
    """Creates a fresh OpenAI client for each request to avoid session staleness."""
    if API_KEY and API_KEY.startswith("sk-or-"):
        return OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=API_KEY,
        )
    elif os.environ.get("OPENROUTER_API_KEY"):
        return OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ.get("OPENROUTER_API_KEY"),
        )
    else:
        print("FATAL ERROR: OPENROUTER_API_KEY is not set or invalid in summarization_pipeline.py.", file=sys.stderr)
        return None

# -----------------------------------------------------------------
# --- SIMPLIFIED PROMPT TEMPLATES (SYSTEM INSTRUCTIONS ONLY) ---
# -----------------------------------------------------------------

SYSTEM_INSTRUCTION_TIER_1 = """
You are a hyper-efficient academic assistant creating a **CONCISE CHEAT SHEET**.

**[CRITICAL MISSION]:**
1. **LANGUAGE:** {lang_instruction}
2. **WORD COUNT:** Your target is **{target_words} words**. This is a **HARD LIMIT**.Do not exceed it. Brevity is essential.
3. **CONTENT:** Extract **ONLY** the most critical information: definitions, formulas, and key rules. 
**ABSOLUTELY NO** examples, explanations, or conversational fluff.
4. **FORMATTING:**
   - Use `### Title` for sections.
   - **MANDATORY:** Insert a **BLANK LINE** before and after every `### Title`.
   - Use nested bullets (`-`) and **bold** key terms.
   - Preserve LaTeX formulas ($...$).
   - **DO NOT** write paragraphs. Use bullet points under titles.
"""

SYSTEM_INSTRUCTION_TIER_2 = """
You are an expert professor creating a **STANDARD STUDY GUIDE**.

**[STRICT GUIDELINES]:**
1. **LANGUAGE:** {lang_instruction}
2. **WORD COUNT:** Target approximately **{target_words} words**.
 It is critical you **DO NOT** significantly exceed this limit.
3. **CONTENT:** Synthesize a coherent study guide. Merge duplicate concepts. Include key examples only when essential for understanding.
4. **FORMATTING:**
   - Use `### Title` for every main topic.
   - **MANDATORY:** Insert a **BLANK LINE** before and after every `### Title`.
   - Use standard bullets (`-`) with proper indentation.
   - **DO NOT** write a single large block of text. Break up content logically.
   - Preserve LaTeX formulas ($...$).
"""

SYSTEM_INSTRUCTION_TIER_3 = """
You are an expert editor compiling a **COMPREHENSIVE SUMMARY**.

**[STRICT RULES]:**
1. **LANGUAGE LOCK:** {lang_instruction}
2. **LENGTH:** MINIMUM **{target_words} words**. Be detailed and expansive.
3. **CONTENT:** Detailed explanation of all topics. Retain substance.
4. **FORMATTING:**
   - Use `### Title` for main topics.
   - **MANDATORY:** Insert a **BLANK LINE** before and after every `### Title`.
   - **NEVER** output a single paragraph. Break text frequently.
   - Insert a BLANK LINE between every paragraph.
   - Preserve LaTeX formulas ($...$).
"""

# -----------------------------------------------------------------
# --- HELPER FUNCTIONS ---
# -----------------------------------------------------------------

def count_words(text: str) -> int:
    """Counts legitimate words (alphanumeric + Arabic), ignoring markdown syntax."""
    # Remove LaTeX math delimiters
    text = re.sub(r'\$[^$]+\$', '', text)
    text = re.sub(r'\$\$[^$]+\$\$', '', text)
    # Match alphanumeric words (English + Arabic + Numbers), ignoring symbols like *, #, -
    # \w matches [a-zA-Z0-9_] and unicode characters depending on flags, but explicit ranges are safer.
    # We use a broad range to caption Latin and Arabic words.
    words = re.findall(r'\b[\w\u0600-\u06FF]+\b', text)
    return len(words)

def clean_output(text: str) -> str:
    """Cleans junk tokens from the LLM output."""
    junk_tokens = ["<|im_start|>", "<|im_end|>", "system", "/doc", "```json", "```"]
    for token in junk_tokens:
        text = text.replace(token, "")
    return text.strip()

def smart_truncate(text: str, max_words: int) -> str:
    """Truncates text to max_words, stopping at the last complete sentence, preserving formatting."""
    # 1. Check count first to avoid work if not needed
    matches = list(re.finditer(r'\b[\w\u0600-\u06FF]+\b', text))
    count = len(matches)
    
    if count <= max_words:
        return text
    
    # 2. Find the strict cut-off point (end of the max_words-th word)
    # Index is max_words - 1 because list is 0-indexed
    strict_limit_match = matches[max_words - 1] 
    strict_limit_index = strict_limit_match.end()
    
    # 3. Work backwards from there to find the last sentence end (. ! ?)
    # We slice strictly within the limit
    candidate_text = text[:strict_limit_index]
    
    # Find the last occurrence of sentence delimiters
    last_period = max(candidate_text.rfind('.'), candidate_text.rfind('!'), candidate_text.rfind('?'))
    
    # Checks to ensure we don't cut off too much (e.g. if the last period was 200 chars ago)
    # But for now, safety = strict limit behavior. 
    if last_period != -1:
        return candidate_text[:last_period+1]
    
    # Fallback: If no period found (extremely rare long sentence run), just hard cut and add period.
    return candidate_text + "."

def detect_primary_language(text: str) -> str:
    """Detect the primary language of the text."""
    # 1. Remove Code Blocks (```...```) and Inline Code (`...`) to avoid skewing detection
    # Code is usually English, which can hide the fact that the *commentary* is Arabic.
    text_no_code = re.sub(r'```[\s\S]*?```', '', text)
    text_no_code = re.sub(r'`[^`\n]+`', '', text_no_code)

    arabic_chars = len(re.findall(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]', text_no_code))
    latin_chars = len(re.findall(r'[a-zA-Z]', text_no_code))
    
    # Strict Majority Rule (No 0.5 threshold).
    # If Arabic characters outnumber Latin characters in the non-code text, it's Arabic.
    if arabic_chars > latin_chars:
        return "Arabic"
    else:
        return "English"

# -----------------------------------------------------------------
# --- MAIN PIPELINE (SINGLE SHOT) ---
# -----------------------------------------------------------------

def run_single_shot_summary(chunks: list, target_words: int = 600) -> str:
    """
    Simpler, smarter pipeline. Sends all chunks to Gemini in one go.
    """
    # Create a fresh client for this request
    client = get_openai_client()

    if not client:
        return "Error: API client is not configured."
    
    if not chunks:
        return "Error: No content found."

    print(f"--- [Gemini Single-Shot] Summarizing {len(chunks)} chunks... ---")
    
    # 1. Combine all text
    combined_notes = "\n\n".join(chunks)
    
    # 2. Detect Language
    detected_lang = detect_primary_language(combined_notes)
    print(f"    Detected language: {detected_lang}")
    
    # 3. Create Strict Language Instruction
    if detected_lang == "Arabic":
        lang_instruction = "Input is ARABIC. Output must be in **ARABIC**. Keep English/Technical terms in English. **KEEP IT CONCISE.**"
    else:
        # Modified to prevent skipping Arabic content if detected as English (e.g. 51% English)
        lang_instruction = (
            "Input is primarily ENGLISH. "
            "Output must be in **ENGLISH**. "
            "**IMPORTANT:** If the input contains ARABIC segments, summarize them in **ARABIC**."
            "**DO NOT TRANSLATE ARABIC TO ENGLISH.** Keep it in the original language."
        )
    #this variable then goes to the message of the llm during each summary excution
    # to avoid hallucination
    # 4. Choose System Prompt & Adjust Target (Buffer Strategy)
    # To prevent "mid-context" cut-offs (truncation) AND stay under the limit,
    # we target a lower word count so the model finishes naturally with a safety margin.
    
    effective_target_words = target_words

    if target_words <= 300:
        template = SYSTEM_INSTRUCTION_TIER_1
        print("    -> Using TIER 1 System Rules (Cheat Sheet)")
        
        # [Aggressive Buffer for Concise]
        # Models struggle with small limits (they like to talk).
        # We aggressively cut the target to 40% to guarantee strict adherence.
        # Target 300 -> Ask for 120.
        # [Hyper-Aggressive Buffer]
        # Models ignore small limits. We cut the target to 35% to force brevity.
        # Target 300 -> Ask for 105.
        effective_target_words = int(target_words * 0.35)
        print(f"    -> [Hyper-Aggressive Buffer] Tier 1 Target reduced to {effective_target_words} (Limit: {target_words})")

    elif target_words <= 600:
        template = SYSTEM_INSTRUCTION_TIER_2
        print("    -> Using TIER 2 System Rules (Standard)")
        
        # [Standard Buffer] Target 60% of the limit to provide a safe margin.
        # Aiming for ~360 words on a 600-word limit.
        effective_target_words = int(target_words * 0.60)
            
        print(f"    -> [Standard Buffer] Tier 2 Target: {effective_target_words} (Limit: {target_words})")
        
    else:
        template = SYSTEM_INSTRUCTION_TIER_3
        print("    -> Using TIER 3 System Rules (Comprehensive)")
    
    # 5. Format the SYSTEM message with rules
    system_rules = template.format(
        target_words=effective_target_words,
        lang_instruction=lang_instruction
    )
    
    # 6. Prepare the User Content (Just the data)
    user_content = f"Here is the lecture content to summarize:\n\n{combined_notes}"

    print(f"\n--- [DEBUG] SYSTEM RULES ---\n{system_rules}\n--- [DEBUG] END RULES ---\n")

    try:
        # Calculate a dynamic hard token limit to prevent run-on generations.
        # Arabic words are ~2.5 tokens, English ~1.5. We add a 25% safety margin.
        token_per_word = 2.5 if detected_lang == "Arabic" else 1.5
        hard_token_limit = int(target_words * token_per_word * 1.25)
        
        # Set a sensible floor (e.g., 250 tokens) to allow for structure.
        hard_token_limit = max(hard_token_limit, 250)
        print(f"    -> [Safety Net] Hard token limit set to {hard_token_limit}")

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_rules},
                {"role": "user", "content": user_content}
            ],
            max_tokens=hard_token_limit
        )
        
        result = clean_output(response.choices[0].message.content)
        
        # SAFETY NET: Smart Truncate
        # If the model ignored the instructions and went over, we cut it off intelligently.
        final_text = smart_truncate(result, target_words)
        
        print(f"--- [Gemini Single-Shot] Completed. Length: {count_words(final_text)} words. (Limit: {target_words}) ---")
        return final_text

    except Exception as e:
        print(f"Error in Gemini Summary: {e}", file=sys.stderr)
        return f"Error generating summary: {e}"


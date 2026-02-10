import os
import sys
import uuid
import pymupdf  # For PDF reading (PyMuPDF)
import requests
from langchain_text_splitters import RecursiveCharacterTextSplitter
import re
import io
import hashlib
from PIL import Image

from dotenv import load_dotenv
load_dotenv()

# --- Configuration ---
# 1. LOAD HUGGING FACE API KEY FROM ENVIRONMENT
HF_API_KEY = os.environ.get("HF_API_KEY")

# Chunking settings
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100 
# --- End Configuration ---

def get_batch_image_descriptions(images: list) -> list:
    """
    Processes images using Hugging Face Inference API (Free tier).
    """
    if not images:
        return []

    descriptions = []

    # Switched to vit-gpt2 as BLIP-base was returning 410 (Gone) errors
    API_URL = "https://api-inference.huggingface.co/models/nlpconnect/vit-gpt2-image-captioning"
    
    headers = {}
    if HF_API_KEY:
        headers["Authorization"] = f"Bearer {HF_API_KEY}"

    BATCH_SIZE = 10
    print(f"    -> Batch Processing {len(images)} images in groups of {BATCH_SIZE}...")

    for i in range(0, len(images), BATCH_SIZE):
        batch = images[i:i + BATCH_SIZE]
        batch_idx_start = i
        print(f"       Processing batch {batch_idx_start+1}-{batch_idx_start+len(batch)}...")
        
        batch_descriptions = []
        for idx, img_bytes in enumerate(batch):
            try:
                response = requests.post(API_URL, headers=headers, data=img_bytes, timeout=30)
                
                if response.status_code == 200:
                    result = response.json()
                    desc = result.get("generated_text", "[Description unavailable]") if isinstance(result, dict) else "[Description unavailable]"
                    batch_descriptions.append(desc)
                elif response.status_code == 503:
                    # Model loading - wait and retry once
                    import time
                    time.sleep(2)
                    retry_response = requests.post(API_URL, headers=headers, data=img_bytes, timeout=30)
                    if retry_response.status_code == 200:
                        retry_result = retry_response.json()
                        desc = retry_result.get("generated_text", "[Description unavailable]") if isinstance(retry_result, dict) else "[Description unavailable]"
                        batch_descriptions.append(desc)
                    else:
                        batch_descriptions.append("[Model loading, please retry]")
                else:
                    print(f"       [Warning] Status {response.status_code} for image {batch_idx_start+idx+1}")
                    batch_descriptions.append("[Error processing image]")
                    
            except Exception as e:
                print(f"       [Error] Failed to process image {batch_idx_start+idx+1}: {e}")
                batch_descriptions.append("[Error processing image]")
        
        descriptions.extend(batch_descriptions)

    return descriptions

def read_document(file_path: str):
    """Reads text AND images from PDF, using PyMuPDF (fitz) + Hugging Face."""
    print(f"  Reading file: {file_path}")
    _, extension = os.path.splitext(file_path)
    extension = extension.lower()
    
    all_text_content = ""

    collected_images = [] # Store (bytes)
    seen_image_hashes = set() # For deduplication
    image_counter = 0

    try:
        # 1. Strict PDF Check
        if extension != ".pdf":
            print(f"Error: Unsupported file type '{extension}'. Only PDF is supported.", file=sys.stderr)
            return None

        # 2. Process PDF with PyMuPDF
        print(f"  Opening with PyMuPDF...")
        doc = pymupdf.open(file_path)
        
        for page_num, page in enumerate(doc):
            print(f"  Processing page {page_num+1}/{len(doc)}...")
            
            # Get content in "dict" format for blocks
            blocks = page.get_text("dict")["blocks"]
            
            for block in blocks:
                if block["type"] == 0: # TEXT BLOCK
                    block_text = ""
                    for line in block["lines"]:
                        for span in line["spans"]:
                            text = span["text"]
                            text = text.replace("\x00", "")
                            block_text += text + " "
                        block_text += "\n"
                    all_text_content += block_text + "\n"

                elif block["type"] == 1: # IMAGE BLOCK
                     try:
                         image_bytes = block["image"]
                         
                         # 1. Deduplication (Critical for repeated logos/backgrounds)
                         img_hash = hashlib.md5(image_bytes).hexdigest()
                         if img_hash in seen_image_hashes:
                             continue
                         
                         # 2. Strict Size Rules (> 10KB, > 250px)
                         if len(image_bytes) < 10240: # 10KB
                             continue
                             
                         # 3. Smart Dimensions & Aspect Ratio
                         try:
                             with Image.open(io.BytesIO(image_bytes)) as img:
                                 width, height = img.size
                                 
                                 # Rule A: Must be big enough to be a diagram
                                 if width < 250 or height < 250:
                                     continue
                                     
                                 # Rule B: Aspect Ratio (Filter banners/footers)
                                 # If width is 4x height or height is 4x width -> likely decoration
                                 aspect_ratio = max(width, height) / min(width, height)
                                 if aspect_ratio > 3.5:
                                     continue
                                     
                         except:
                             continue

                         # If it passes all tests:
                         seen_image_hashes.add(img_hash)
                         collected_images.append(image_bytes)
                         placeholder = f"<<IMAGE_PLACEHOLDER_{image_counter}>>"
                         all_text_content += f"\n{placeholder}\n"
                         image_counter += 1
                     except Exception as e:
                         print(f"    [Warning] Failed to extract image on page {page_num+1}: {e}")
        
        doc.close()

        # 3. Batch Process Images (If any)
        if collected_images:
            print(f"  Found {len(collected_images)} images. Starting Batch Analysis (Hugging Face)...")
            descriptions = get_batch_image_descriptions(collected_images)
            
            # 4. Replace Placeholders
            print("  Injecting descriptions into text...")
            for i, desc in enumerate(descriptions):
                placeholder = f"<<IMAGE_PLACEHOLDER_{i}>>"
                
                if "IRRELEVANT" in desc.upper() or "[ERROR" in desc.upper():
                    all_text_content = all_text_content.replace(placeholder, "")
                else:
                    formatted_desc = f"\n>>> [IMAGE START] >>>\n{desc}\n<<< [IMAGE END] <<<\n"
                    all_text_content = all_text_content.replace(placeholder, formatted_desc)
        
        # Cleanup
        all_text_content = re.sub(r"<<IMAGE_PLACEHOLDER_\d+>>", "", all_text_content)
        
        return all_text_content

    except Exception as e:
        print(f"Error reading file {file_path}: {e}", file=sys.stderr)
        return None

# --- Main Ingestion Function ---
def process_and_store_lecture(file_path: str, title: str, subject_name: str):
    from db_dynamo import get_dynamodb_resource
    import time
    
    print(f"\n--- [DynamoDB Ingestion Pipeline] Started for: {title} ---")
    
    # 1. Read the document text
    print("Step 1: Reading document/Images...")
    document_text = read_document(file_path)
    if not document_text:
        raise ValueError("Pipeline failed: Could not read document or document is empty.")

    # 2. Split text into chunks
    print("Step 2: Chunking text...")
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        length_function=len,
        separators=["\n\n", ". ", " ", ""]
    )
    chunks = text_splitter.split_text(document_text)
    print(f"  Split into {len(chunks)} chunks.")
    if not chunks:
        raise ValueError("Pipeline failed: No text chunks generated.")
        
    # 3. Connect to DynamoDB
    print("Step 3: Connecting to DynamoDB...")
    dynamodb = get_dynamodb_resource()
    if not dynamodb:
        raise ValueError("Could not connect to AWS DynamoDB.")
        
    table_lectures = dynamodb.Table('Lectures')
    table_chunks = dynamodb.Table('LectureChunks')
    
    try:
        # 5. Store Lecture Metadata and Chunks
        # Note: We don't look up Subject_ID anymore; we just store the subject name or ensure the subject exists in the app logic.
        # But for consistency with the NoSQL design, we'll store specific metadata.
        
        lecture_id = f"{title.lower().replace(' ', '-')}-{str(uuid.uuid4())[:8]}"
        timestamp = str(time.time())
        
        print(f"Step 5: Storing lecture data (ID: {lecture_id})...")
        
        # A. Insert Metadata
        table_lectures.put_item(Item={
            'lecture_id': lecture_id,
            'subject_name': subject_name, # Storing name directly for easier Query
            'title': title,
            'original_filename': os.path.basename(file_path),
            'upload_timestamp': timestamp
        })
        
        # B. Insert Chunks (Batch Insert)
        print("Step 6: Inserting chunks into DynamoDB (Batch)...")
        
        with table_chunks.batch_writer() as batch:
            for i, chunk in enumerate(chunks):
                batch.put_item(Item={
                    'lecture_id': lecture_id,
                    'chunk_index': i,
                    'chunk_text': chunk
                })

        print("  Success! Lecture and chunks saved to DynamoDB.")

    except Exception as e:
        print(f"Pipeline failed: {e}", file=sys.stderr)
        raise e 

    print(f"--- [Ingestion Finished for: {title}] ---")



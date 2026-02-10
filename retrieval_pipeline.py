import sys

# --- Configuration ---
# DB_FILE removed (Using DynamoDB)
# --- End Configuration ---

def retrieve_chunks_for_lecture(lecture_id_to_find: str) -> list:
    """
    Retrieves all text chunks for a specific lecture_id from DynamoDB.
    """
    from db_dynamo import get_dynamodb_resource
    from boto3.dynamodb.conditions import Key
    
    print(f"--- [Retrieval Module] Fetching chunks for ID: {lecture_id_to_find} ---")
    
    try:
        dynamodb = get_dynamodb_resource()
        if not dynamodb:
            print("Error: Could not connect to DynamoDB.", file=sys.stderr)
            return []
            
        table = dynamodb.Table('LectureChunks')
        
        # 1. Query DynamoDB
        # We query the partition key (lecture_id) and the sort key acts as ordering.
        response = table.query(
            KeyConditionExpression=Key('lecture_id').eq(lecture_id_to_find)
        )
        
        items = response.get('Items', [])
        
        # 2. Sort by chunk_index (DynamoDB usually returns sorted if queried by SK, but good to be safe)
        items.sort(key=lambda x: int(x['chunk_index']))
        
        # 3. Extract text
        all_text_chunks = [item['chunk_text'] for item in items]

    except Exception as e:
        print(f"Error: Failed to query DynamoDB. {e}", file=sys.stderr)
        return []

    if not all_text_chunks:
        print(f"Warning: No chunks found for ID '{lecture_id_to_find}'.")
    else:
        print(f"Success: Found {len(all_text_chunks)} text chunks.")
    
    return all_text_chunks


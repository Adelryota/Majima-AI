import boto3
import sys
import os
import bcrypt
from botocore.exceptions import ClientError

# --- Configuration ---
AWS_REGION = "us-east-1"  # Default region, can be changed
DYNAMO_ENDPOINT = None    # Set to 'http://localhost:8000' if using DynamoDB Local

def get_dynamodb_resource():
    """
    Returns a boto3 DynamoDB resource.
    Uses environment variables AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY automatically.
    """
    # DEBUG: Check if keys exist (don't print values)
    if not os.environ.get('AWS_ACCESS_KEY_ID'):
        print("DEBUG: AWS_ACCESS_KEY_ID is MISSING from environment!", file=sys.stderr)
    else:
        print(f"DEBUG: AWS_ACCESS_KEY_ID found: {os.environ.get('AWS_ACCESS_KEY_ID')[:4]}***", file=sys.stderr)

    try:
        # Explicitly pass credentials if available (fixes some platform issues)
        aws_access_key_id = os.environ.get('AWS_ACCESS_KEY_ID')
        aws_secret_access_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        
        if aws_access_key_id and aws_secret_access_key:
             return boto3.resource(
                'dynamodb', 
                region_name=AWS_REGION, 
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                endpoint_url=DYNAMO_ENDPOINT
            )
        else:
            return boto3.resource('dynamodb', region_name=AWS_REGION, endpoint_url=DYNAMO_ENDPOINT)
    except Exception as e:
        print(f"Error connecting to AWS DynamoDB: {e}", file=sys.stderr)
        return None

def create_tables_if_not_exist():
    """
    Checks for required tables and creates them if missing.
    """
    dynamodb = get_dynamodb_resource()
    if not dynamodb:
        print("Failed to connect to DynamoDB. Check credentials.")
        return

    tables = {
        'Users': {'pk': 'username', 'sk': None},
        'Subjects': {'pk': 'name', 'sk': None},
        'Lectures': {'pk': 'lecture_id', 'sk': None}, # Metadata
        'LectureChunks': {'pk': 'lecture_id', 'sk': 'chunk_index'}, # Content
        'Summaries': {'pk': 'lecture_id', 'sk': 'summary_type'} # Cache
    }

    existing_tables = [t.name for t in dynamodb.tables.all()]
    print(f"Existing DynamoDB Tables: {existing_tables}")

    for table_name, schema in tables.items():
        if table_name not in existing_tables:
            print(f"Creating table: {table_name}...")
            
            key_schema = [{'AttributeName': schema['pk'], 'KeyType': 'HASH'}]
            attr_defs = [{'AttributeName': schema['pk'], 'AttributeType': 'S' if schema['pk'] != 'chunk_index' else 'N'}]

            if schema['sk']:
                key_schema.append({'AttributeName': schema['sk'], 'KeyType': 'RANGE'})
                # chunk_index is Number (N), summary_type is String (S) (e.g. '600')
                sk_type = 'N' if schema['sk'] == 'chunk_index' else 'S'
                attr_defs.append({'AttributeName': schema['sk'], 'AttributeType': sk_type})

            # Special case for chunk_index (it is a number, but we defined PK as S above, let's correct logic)
            # Users: username (S)
            # Subjects: name (S)
            # Lectures: lecture_id (S)
            # LectureChunks: lecture_id (S), chunk_index (N)
            # Summaries: lecture_id (S), summary_type (S)
            
            # Correcting AttributeType logic manually for safety
            if table_name == 'LectureChunks':
                 attr_defs = [
                     {'AttributeName': 'lecture_id', 'AttributeType': 'S'},
                     {'AttributeName': 'chunk_index', 'AttributeType': 'N'}
                 ]
            
            try:
                table = dynamodb.create_table(
                    TableName=table_name,
                    KeySchema=key_schema,
                    AttributeDefinitions=attr_defs,
                    BillingMode='PAY_PER_REQUEST' # On-Demand (Free Tier friendly for low scale)
                )
                table.wait_until_exists()
                print(f"  -> Table '{table_name}' created successfully.")
            except ClientError as e:
                print(f"  -> Error creating {table_name}: {e}")
        else:
            print(f"Table '{table_name}' already exists.")

    print("\n--- Verifying Admin User ---")
    setup_admin_user(dynamodb)

def setup_admin_user(dynamodb):
    table = dynamodb.Table('Users')
    try:
        # Check if admin exists
        response = table.get_item(Key={'username': 'admin'})
        if 'Item' not in response:
            print("Creating default 'admin' user...")
            hashed = bcrypt.hashpw('admin'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            table.put_item(Item={
                'username': 'admin',
                'password_hash': hashed,
                'role': 'admin'
            })
            print("Admin user created (Pass: admin)")
    except Exception as e:
        print(f"Error checking admin user: {e}")

# --- Helper: Deep Delete (Cascade) ---
def delete_lecture_fully(lecture_id):
    """
    Deletes a lecture, its chunks, and its summaries.
    DynamoDB does not have Foreign Key Cascades, so we do it manually.
    """
    dynamodb = get_dynamodb_resource()
    from boto3.dynamodb.conditions import Key
    
    # 1. Delete Metadata
    dynamodb.Table('Lectures').delete_item(Key={'lecture_id': lecture_id})
    
    # 2. Delete Chunks (Query & Batch Delete)
    table_chunks = dynamodb.Table('LectureChunks')
    # We only have PK=lecture_id. We can Query it.
    # To delete, we need the Sort Key (chunk_index) as well.
    try:
        scan_chunks = table_chunks.query(KeyConditionExpression=Key('lecture_id').eq(lecture_id))
        with table_chunks.batch_writer() as batch:
            for item in scan_chunks.get('Items', []):
                batch.delete_item(Key={'lecture_id': lecture_id, 'chunk_index': item['chunk_index']})
    except Exception as e:
        print(f"Error deleting chunks for {lecture_id}: {e}")

    # 3. Delete Summaries
    table_summaries = dynamodb.Table('Summaries')
    try:
        scan_sum = table_summaries.query(KeyConditionExpression=Key('lecture_id').eq(lecture_id))
        with table_summaries.batch_writer() as batch:
            for item in scan_sum.get('Items', []):
                batch.delete_item(Key={'lecture_id': lecture_id, 'summary_type': item['summary_type']})
    except Exception as e:
        print(f"Error deleting summaries for {lecture_id}: {e}")

if __name__ == "__main__":
    create_tables_if_not_exist()

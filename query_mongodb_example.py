#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "pymongo",
# ]
# ///

"""
Example script to query MongoDB data from Claude hooks.
"""

import os
from pathlib import Path
from pymongo import MongoClient
from datetime import datetime, timedelta

def get_mongo_client():
    """Get MongoDB client with configurable URI."""
    mongo_uri = os.getenv('CLAUDE_MONGO_URI', 'mongodb://localhost:27017/')
    return MongoClient(mongo_uri)

def get_database_name():
    """Generate database name from project directory name."""
    # Use just the project directory name (last part of path)
    project_name = Path.cwd().name
    # Sanitize for MongoDB (remove special chars)
    safe_name = project_name.replace('.', '_').replace('-', '_')
    return f"claudehooks_{safe_name}"

def print_section(title):
    """Print a formatted section header."""
    print(f"\n{'=' * 80}")
    print(f" {title}")
    print('=' * 80)

def query_tool_usage():
    """Query and display tool usage statistics."""
    print_section("Tool Usage Statistics")

    client = get_mongo_client()
    db = client[get_database_name()]
    collection = db['tool_use']

    # Count tools by name
    pipeline = [
        {'$match': {'status': 'complete'}},
        {'$group': {'_id': '$tool_name', 'count': {'$sum': 1}}},
        {'$sort': {'count': -1}}
    ]

    results = list(collection.aggregate(pipeline))

    print("\nTool execution counts:")
    for result in results:
        print(f"  {result['_id']}: {result['count']}")

    # Get recent tool uses
    print("\nRecent tool executions:")
    recent = collection.find({'status': 'complete'}).sort('timestamp_post', -1).limit(5)
    for doc in recent:
        tool_name = doc.get('tool_name', 'Unknown')
        timestamp = doc.get('timestamp_post', 'Unknown')
        print(f"  {timestamp} - {tool_name}")

    client.close()

def query_sessions():
    """Query and display session information."""
    print_section("Session Information")

    client = get_mongo_client()
    db = client[get_database_name()]

    # Get recent sessions
    start_collection = db['session_start']
    stop_collection = db['session_stop']

    print("\nRecent sessions:")
    sessions = start_collection.find().sort('timestamp', -1).limit(5)
    for session in sessions:
        session_id = session.get('session_id', 'Unknown')[:8]
        source = session.get('source', 'Unknown')
        timestamp = session.get('timestamp', 'Unknown')
        print(f"  {timestamp} - Session {session_id}... ({source})")

    # Count total sessions
    total_starts = start_collection.count_documents({})
    total_stops = stop_collection.count_documents({})
    print(f"\nTotal session starts: {total_starts}")
    print(f"Total session stops: {total_stops}")

    client.close()

def query_user_prompts():
    """Query and display recent user prompts."""
    print_section("Recent User Prompts")

    client = get_mongo_client()
    db = client[get_database_name()]
    collection = db['user_prompt']

    prompts = collection.find().sort('timestamp', -1).limit(5)

    for i, prompt_doc in enumerate(prompts, 1):
        timestamp = prompt_doc.get('timestamp', 'Unknown')
        prompt = prompt_doc.get('prompt', '')
        # Truncate long prompts
        if len(prompt) > 100:
            prompt = prompt[:100] + '...'
        print(f"\n{i}. {timestamp}")
        print(f"   {prompt}")

    client.close()

def query_notifications():
    """Query and display notifications."""
    print_section("Recent Notifications")

    client = get_mongo_client()
    db = client[get_database_name()]
    collection = db['notification']

    notifications = collection.find().sort('timestamp', -1).limit(5)

    for notif in notifications:
        timestamp = notif.get('timestamp', 'Unknown')
        message = notif.get('message', 'Unknown')
        print(f"  {timestamp} - {message}")

    client.close()

def query_database_info():
    """Display database and collection information."""
    print_section("Database Information")

    client = get_mongo_client()
    db_name = get_database_name()
    db = client[db_name]

    print(f"\nDatabase: {db_name}")
    print(f"MongoDB URI: {os.getenv('CLAUDE_MONGO_URI', 'mongodb://localhost:27017/')}")

    print("\nCollections:")
    for collection_name in sorted(db.list_collection_names()):
        count = db[collection_name].count_documents({})
        print(f"  {collection_name}: {count} documents")

    client.close()

def main():
    """Main function to run all queries."""
    try:
        query_database_info()
        query_sessions()
        query_tool_usage()
        query_user_prompts()
        query_notifications()

        print("\n" + "=" * 80)
        print("\nQuery completed successfully!")

    except Exception as e:
        print(f"\nError connecting to MongoDB: {e}")
        print("\nMake sure MongoDB is running and accessible at:")
        print(f"  {os.getenv('CLAUDE_MONGO_URI', 'mongodb://localhost:27017/')}")

if __name__ == '__main__':
    main()

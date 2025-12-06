import requests
import json
import sys
import argparse
import os
from pathlib import Path
from datetime import datetime
from tqdm import tqdm  # Import the progress bar library

# ==========================================
# DEFAULT CONFIGURATION
# ==========================================
SCRIPT_DIR = Path(__file__).parent.absolute()
CONFIG_FILE = SCRIPT_DIR / "config.json"

DEFAULT_URL = "http://localhost:8096"
DEFAULT_USER = "admin" 

def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {}

def save_config(url, key, user):
    data = {
        "url": url,
        "key": key,
        "user": user
    }
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"Configuration saved to '{CONFIG_FILE}'.")
    except IOError as e:
        print(f"Error saving config: {e}")

def get_default_filename():
    now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"jellyfin_backup_{now}.json"

def parse_arguments(config):
    parser = argparse.ArgumentParser(description="Export Jellyfin watched items to a JSON file.")
    
    parser.add_argument("--url", "-s", default=None, help="Jellyfin Server URL")
    parser.add_argument("--key", "-k", default=None, help="Jellyfin API Key")
    parser.add_argument("--user", "-u", default=None, help="Username to backup")
    parser.add_argument("--filename", "-f", default=None, help="Output filename")
    parser.add_argument("--save", action="store_true", help="Save settings to config.json")

    args = parser.parse_args()

    final_args = argparse.Namespace()
    final_args.url = args.url or config.get("url") or DEFAULT_URL
    final_args.key = args.key or config.get("key")
    final_args.user = args.user or config.get("user") or DEFAULT_USER
    final_args.filename = args.filename or get_default_filename()
    final_args.save = args.save
    return final_args

def get_headers(api_key):
    return {
        "X-Emby-Token": api_key,
        "Content-Type": "application/json"
    }

def get_user_id(base_url, headers, username):
    try:
        url = f"{base_url}/Users"
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        for user in response.json():
            if user['Name'] == username:
                return user['Id']
        print(f"Error: User '{username}' not found.")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"Connection Error: {e}")
        sys.exit(1)

def get_watched_items(base_url, headers, user_id):
    print(f"Fetching watched items for User ID: {user_id}...")
    url = f"{base_url}/Users/{user_id}/Items"
    params = {
        "Recursive": "true",
        "Filters": "IsPlayed",
        "IncludeItemTypes": "Movie,Episode",
        "Fields": "ProviderIds,DateCreated,Path,OriginalTitle,SeriesName,SeasonName,MediaSources",
        "SortBy": "DatePlayed",
        "SortOrder": "Descending"
    }
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json().get('Items', [])
    except requests.exceptions.RequestException as e:
        print(f"Error fetching items: {e}")
        sys.exit(1)

def save_to_current_folder(items, filename):
    clean_data = []
    
    # === PROGRESS BAR ADDED HERE ===
    # We wrap 'items' with tqdm() to create the bar
    print("Processing items...")
    for item in tqdm(items, desc="Exporting", unit="item"):
        file_size = 0
        container = "unknown"
        if item.get("MediaSources"):
            source = item["MediaSources"][0]
            file_size = source.get("Size", 0)
            container = source.get("Container", "unknown")

        entry = {
            "Title": item.get("Name"),
            "OriginalTitle": item.get("OriginalTitle"),
            "Type": item.get("Type"),
            "Year": item.get("ProductionYear"),
            "ProviderIds": item.get("ProviderIds", {}),
            "PlayedStatus": item.get("UserData", {}).get("Played", False),
            "LastPlayedDate": item.get("UserData", {}).get("LastPlayedDate"),
            "PlayCount": item.get("UserData", {}).get("PlayCount", 0),
            "Path": item.get("Path"),
            "FileSize": file_size,
            "Container": container
        }
        
        if item.get("Type") == "Episode":
            entry["ShowName"] = item.get("SeriesName") 
            entry["SeasonName"] = item.get("SeasonName")

        clean_data.append(entry)

    output_path = SCRIPT_DIR / filename

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(clean_data, f, indent=4, ensure_ascii=False)
        # Clear line to make final message cleaner
        print(f"\nSuccess! Saved {len(clean_data)} items to: {output_path}")
    except IOError as e:
        print(f"Error saving file: {e}")

if __name__ == "__main__":
    config = load_config()
    args = parse_arguments(config)
    
    if not args.key:
        print("Error: API Key is missing. Provide it via --key or save it to config.json.")
        sys.exit(1)

    if args.save:
        save_config(args.url, args.key, args.user)

    server_url = args.url.rstrip('/')
    headers = get_headers(args.key)
    
    user_id = get_user_id(server_url, headers, args.user)
    watched_items = get_watched_items(server_url, headers, user_id)
    
    if watched_items:
        save_to_current_folder(watched_items, args.filename)
    else:
        print("No watched items found.")

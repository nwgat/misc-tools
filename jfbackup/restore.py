import requests
import json
import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

# ==========================================
# DEFAULT CONFIGURATION
# ==========================================
SCRIPT_DIR = Path(__file__).parent.absolute()
CONFIG_FILE = SCRIPT_DIR / "config.json"

DEFAULT_URL = "http://localhost:8096"
DEFAULT_USER = "admin" 
FALLBACK_FILENAME = "jellyfin_watched_backup.json"

def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {}

def save_config(url, key, user):
    """Saves url/key/user. We do NOT save filename so auto-discovery works next time."""
    data = { "url": url, "key": key, "user": user }
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"Configuration saved to '{CONFIG_FILE}'.")
    except IOError as e:
        print(f"Error saving config: {e}")

def find_latest_backup():
    """Scans the SCRIPT FOLDER for the newest 'jellyfin_backup_*.json' file."""
    candidates = list(SCRIPT_DIR.glob("jellyfin_backup_*.json"))
    
    if not candidates:
        return None
        
    # Sort by modification time (Newest first)
    candidates.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    
    latest = candidates[0].name
    print(f"Auto-detected latest backup: {latest}")
    return latest

def parse_arguments(config):
    parser = argparse.ArgumentParser(description="Import Jellyfin watched status.")
    parser.add_argument("--url", "-s", default=None, help="Target Jellyfin Server URL")
    parser.add_argument("--key", "-k", default=None, help="Target Jellyfin API Key")
    parser.add_argument("--user", "-u", default=None, help="Target Username")
    parser.add_argument("--filename", "-f", default=None, help="Input JSON filename")
    parser.add_argument("--commit", action="store_true", help="Actually apply changes.")
    parser.add_argument("--save", action="store_true", help="Save url/key/user to config.json")
    
    args = parser.parse_args()
    
    final_args = argparse.Namespace()
    final_args.url = args.url or config.get("url") or DEFAULT_URL
    final_args.key = args.key or config.get("key")
    final_args.user = args.user or config.get("user") or DEFAULT_USER
    final_args.commit = args.commit
    final_args.save = args.save

    # === FILENAME SELECTION LOGIC (UPDATED) ===
    fname = args.filename
    
    # 1. Check Config, but verify it exists
    if not fname:
        config_fname = config.get("filename")
        if config_fname:
            # Only use config filename if it ACTUALLY exists
            if (SCRIPT_DIR / config_fname).exists():
                fname = config_fname
            # If it doesn't exist, we ignore it and let auto-discovery run
    
    # 2. Auto-Discovery
    if not fname: 
        fname = find_latest_backup()
        
    # 3. Fallback
    if not fname: 
        fname = FALLBACK_FILENAME
        
    final_args.filename = fname
    return final_args

def get_headers(api_key):
    return { "X-Emby-Token": api_key, "Content-Type": "application/json" }

def get_user_id(base_url, headers, username):
    try:
        url = f"{base_url}/Users"
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        for user in response.json():
            if user['Name'] == username:
                return user['Id']
        print(f"Error: Target user '{username}' not found.")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"Connection Error: {e}")
        sys.exit(1)

def get_all_library_items(base_url, headers, user_id):
    print("Fetching target server library...")
    url = f"{base_url}/Users/{user_id}/Items"
    params = {
        "Recursive": "true",
        "IncludeItemTypes": "Movie,Episode",
        "Fields": "ProviderIds,ProductionYear,OriginalTitle,SeriesName", 
    }
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        items = response.json().get('Items', [])
        print(f"Server library loaded: {len(items)} items found.")
        return items
    except requests.exceptions.RequestException as e:
        print(f"Error fetching library: {e}")
        sys.exit(1)

def mark_as_watched(base_url, headers, user_id, item_id, date_played):
    url = f"{base_url}/Users/{user_id}/PlayedItems/{item_id}"
    data = {}
    if date_played:
        data["DatePlayed"] = date_played

    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"  [X] Failed to mark item {item_id}: {e}")
        return False

def build_lookup_map(library_items):
    lookup = { "imdb": {}, "tvdb": {}, "movie_title_year": {}, "episode_show_title": {} }
    for item in library_items:
        item_id = item['Id']
        p_ids = item.get('ProviderIds', {})
        
        if 'Imdb' in p_ids: lookup['imdb'][p_ids['Imdb']] = item_id
        if 'Tvdb' in p_ids: lookup['tvdb'][p_ids['Tvdb']] = item_id
            
        name = item.get('Name', '').lower().strip()
        item_type = item.get('Type')
        
        if item_type == "Movie":
            year = item.get('ProductionYear')
            if name and year: lookup['movie_title_year'][f"{name}|{year}"] = item_id
        elif item_type == "Episode":
            show_name = item.get('SeriesName', '').lower().strip()
            if name and show_name: lookup['episode_show_title'][f"{show_name}|{name}"] = item_id
    return lookup

def find_match(saved_item, lookup_map):
    saved_imdb = saved_item.get('ProviderIds', {}).get('Imdb')
    if saved_imdb and saved_imdb in lookup_map['imdb']: return lookup_map['imdb'][saved_imdb], "IMDB Match"

    saved_tvdb = saved_item.get('ProviderIds', {}).get('Tvdb')
    if saved_tvdb and saved_tvdb in lookup_map['tvdb']: return lookup_map['tvdb'][saved_tvdb], "TVDB Match"

    name = saved_item.get('Title', '').lower().strip()
    item_type = saved_item.get('Type')
    
    if item_type == "Movie":
        year = saved_item.get('Year')
        if name and year:
            key = f"{name}|{year}"
            if key in lookup_map['movie_title_year']: return lookup_map['movie_title_year'][key], "Movie Title+Year Match"
    elif item_type == "Episode":
        show_name = saved_item.get('ShowName', '').lower().strip() or saved_item.get('SeriesName', '').lower().strip()
        if name and show_name:
            key = f"{show_name}|{name}"
            if key in lookup_map['episode_show_title']: return lookup_map['episode_show_title'][key], "Episode Show+Title Match"
    return None, None

def sort_by_date(items):
    def get_date(item):
        date_str = item.get('LastPlayedDate')
        if not date_str: return datetime.min.replace(tzinfo=timezone.utc)
        try:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            if dt.tzinfo is None: return dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError: return datetime.min.replace(tzinfo=timezone.utc)
    
    print("Sorting items by date watched (Oldest first)...")
    return sorted(items, key=get_date)

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
    
    backup_path = SCRIPT_DIR / args.filename
    if not backup_path.exists(): 
        backup_path = Path(args.filename)
    
    if not backup_path.exists():
        print(f"Error: Backup file not found at {backup_path}")
        print("Available files in directory:")
        for f in SCRIPT_DIR.glob("*.json"):
            print(f" - {f.name}")
        sys.exit(1)
        
    print(f"Reading backup from: {backup_path}")
    with open(backup_path, 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    backup_data = sort_by_date(raw_data)
    user_id = get_user_id(server_url, headers, args.user)
    library_items = get_all_library_items(server_url, headers, user_id)
    lookup_map = build_lookup_map(library_items)

    success_count = 0
    skip_count = 0
    
    print("\nStarting Import...")
    if not args.commit: print("!!! DRY RUN MODE - No changes will be made (use --commit to apply) !!!\n")

    for item in backup_data:
        if not item.get('PlayedStatus'): continue
        match_id, match_type = find_match(item, lookup_map)
        
        title_display = item.get('Title')
        if item.get('Type') == 'Episode' and item.get('ShowName'):
            title_display = f"{item.get('ShowName')} - {title_display}"

        if match_id:
            if args.commit:
                if mark_as_watched(server_url, headers, user_id, match_id, item.get('LastPlayedDate')):
                    print(f"[OK] {title_display}")
                    success_count += 1
            else:
                date_display = item.get('LastPlayedDate', 'Unknown Date')
                print(f"[Dry Run] ({date_display}) Would mark '{title_display}' via {match_type}")
                success_count += 1
        else:
            print(f"[Skip] Could not find match for: {title_display}")
            skip_count += 1

    print("-" * 30)
    print(f"{'Import' if args.commit else 'Dry Run'} Complete. {success_count} restored. {skip_count} skipped.")

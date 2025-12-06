import sys
import os
import re
import platform
import tarfile
import zipfile
import shutil
from datetime import datetime

# --- Dependency Check ---
try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("\n------------------------------------------------------------")
    print("Error: Required Python modules are missing.")
    print("Please install them by running one of the following commands:")
    print("\n    pip3 install bs4 requests")
    
    if os.name == 'nt': # Windows specific check
        print("\n    # Or if 'pip3' is not recognized on Windows:")
        print("    python -m pip install bs4 requests")
        
    print("\n------------------------------------------------------------")
    sys.exit(1)
# ------------------------

def get_firmware_list_with_dates(url):
    """
    Fetches the list of firmware files and their modification dates from the deCONZ page.
    """
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        firmware_data = []
        
        # Find all <a> tags that link to firmware files
        firmware_links = soup.find_all('a', href=re.compile(r'\.bin(?:\.GCF)?$'))

        for link in firmware_links:
            filename = link['href']
            
            # The date and time are in the text node that immediately follows the <a> tag.
            if link.next_sibling and isinstance(link.next_sibling, str):
                line_info = link.next_sibling.strip()
                match = re.search(r'(\d{2}-[A-Za-z]{3}-\d{4}\s\d{2}:\d{2})', line_info)
                if match:
                    date_str = match.group(1)
                    try:
                        date_obj = datetime.strptime(date_str, '%d-%b-%Y %H:%M')
                        firmware_data.append((filename, date_obj))
                    except ValueError:
                        continue
                        
        if not firmware_data:
            print("Debug: Parsing completed, but no firmware data was extracted.")
            return None

        return firmware_data
    except requests.exceptions.RequestException as e:
        print(f"Error: Could not fetch firmware list from {url}. Reason: {e}")
        return None
    except Exception as e:
        print(f"An error occurred while parsing the firmware page: {e}")
        return None

def extract_archive(filepath, extract_to_path):
    """
    Extracts .tar.gz or .zip files and prepares the GCFFlasher binary.
    """
    archive_name = os.path.basename(filepath)
    temp_extract_path = os.path.join(extract_to_path, "temp_extraction")
    
    # Clean temp path if it exists
    if os.path.exists(temp_extract_path):
        shutil.rmtree(temp_extract_path)
    os.makedirs(temp_extract_path, exist_ok=True)

    print(f"Extracting {archive_name}...")
    
    try:
        if filepath.endswith('.tar.gz'):
            with tarfile.open(filepath, "r:gz") as tar:
                # Fix for Python 3.12+ security warning
                try:
                    tar.extractall(path=temp_extract_path, filter='data')
                except TypeError:
                    tar.extractall(path=temp_extract_path)
        elif filepath.endswith('.zip'):
            with zipfile.ZipFile(filepath, 'r') as zip_ref:
                zip_ref.extractall(temp_extract_path)

        # Search for the GCFFlasher binary (or .exe)
        flasher_src = None
        flasher_filename = "GCFFlasher"
        
        # Adjust for Windows executable name
        if platform.system().lower() == 'windows':
            flasher_filename = "GCFFlasher.exe"

        for root, dirs, files in os.walk(temp_extract_path):
            # Case insensitive search for the binary
            for file in files:
                if file.lower() == flasher_filename.lower():
                    flasher_src = os.path.join(root, file)
                    break
            if flasher_src:
                break
        
        if flasher_src:
            destination_path = os.path.join(extract_to_path, os.path.basename(flasher_src))
            print(f"Found {os.path.basename(flasher_src)}, moving it to: {destination_path}")
            
            # Remove existing file if it exists to avoid errors
            if os.path.exists(destination_path):
                os.remove(destination_path)
                
            shutil.move(flasher_src, destination_path)
            print(f"Successfully configured GCFFlasher.")
        else:
            print(f"Warning: {flasher_filename} not found within the extracted archive.")

    except Exception as e:
        print(f"Error during extraction: {e}")
    finally:
        # Cleanup
        if os.path.exists(temp_extract_path):
            shutil.rmtree(temp_extract_path)

def download_file(url, save_path='.'):
    """
    Downloads a specific file with a native text-based progress bar.
    """
    filename = url.split('/')[-1]
    local_filepath = os.path.join(save_path, filename)
    
    try:
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))
            
            print(f"\nDownloading: {filename}")
            print(f"From: {url}")
            print(f"To: {local_filepath}")

            downloaded_size = 0
            with open(local_filepath, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    size = f.write(chunk)
                    downloaded_size += size
                    
                    if total_size > 0:
                        percent = int(downloaded_size * 100 / total_size)
                        bar_length = 30
                        filled_length = int(bar_length * downloaded_size // total_size)
                        bar = '#' * filled_length + '-' * (bar_length - filled_length)
                        sys.stdout.write(f"\rProgress: [{bar}] {percent}%")
                        sys.stdout.flush()
                    else:
                        sys.stdout.write(f"\rDownloaded: {downloaded_size} bytes")
                        sys.stdout.flush()

        print(f"\nDownload complete! Saved as {local_filepath}")
        
        if local_filepath.endswith('.tar.gz') or local_filepath.endswith('.zip'):
            extract_archive(local_filepath, save_path)
            os.remove(local_filepath) # remove the archive after extraction
            
    except requests.exceptions.RequestException as e:
        print(f"\nError: Could not download the file. Reason: {e}")
    except IOError as e:
        print(f"\nError: Could not write the file to disk. Reason: {e}")

def download_latest_gcfflasher(save_path='.'):
    """
    Handles the process of downloading the latest GCFFlasher release.
    Checks the last 5 releases for a compatible file.
    """
    # Fetch last 5 releases to be safe
    API_URL = "https://api.github.com/repos/dresden-elektronik/gcfflasher/releases?per_page=5"
    
    try:
        response = requests.get(API_URL, timeout=10)
        response.raise_for_status()
        releases = response.json()
        
        system = platform.system().lower()
        machine = platform.machine().lower()
        
        target_asset = None
        target_release_tag = None
        target_release_date = "Unknown Date"

        print(f"Searching last {len(releases)} releases for a compatible GCFFlasher...")

        for release in releases:
            assets = release.get('assets', [])
            tag_name = release.get('tag_name')
            
            found_asset = None
            if system == 'linux':
                if 'aarch64' in machine or 'arm64' in machine:
                    found_asset = next((a for a in assets if 'linux_arm64' in a['name'].lower() and '.tar.gz' in a['name'].lower()), None)
                elif 'x86_64' in machine:
                    found_asset = next((a for a in assets if 'linux_amd64' in a['name'].lower() and '.tar.gz' in a['name'].lower()), None)
            elif system == 'windows':
                # Look for .zip OR .exe for Windows
                found_asset = next((a for a in assets if 'win' in a['name'].lower() and ('.zip' in a['name'].lower() or '.exe' in a['name'].lower())), None)
            
            if found_asset:
                target_asset = found_asset
                target_release_tag = tag_name
                
                published_at = release.get('published_at')
                if published_at:
                    try:
                        dt_obj = datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%SZ")
                        target_release_date = dt_obj.strftime("%Y-%m-%d")
                    except ValueError:
                        pass
                break 
        
        if target_asset:
            print(f"\nDetected OS: {system.capitalize()} ({machine})")
            print(f"Found suitable flasher in release {target_release_tag}!")
            print(f"File: {target_asset['name']} (Released: {target_release_date})")
            confirm = input("Do you want to download this file? (y/n): ").lower()
            if confirm == 'y':
                download_file(target_asset['browser_download_url'], save_path=save_path)
            else:
                print("GCFFlasher download cancelled.")
        else:
            print("\nCould not find a suitable GCFFlasher for your OS in the recent releases.")
            print("Please download it manually from: https://github.com/dresden-elektronik/gcfflasher/releases")
            
    except requests.exceptions.RequestException as e:
        print(f"Error: Could not fetch GCFFlasher release info. Reason: {e}")


def filter_and_sort_versions(firmware_data, device_pattern):
    """
    Filters and sorts firmware versions for a specific device.
    """
    device_firmwares = [
        (filename, date) for filename, date in firmware_data 
        if re.search(device_pattern, filename, re.IGNORECASE)
    ]
    if not device_firmwares:
        return []
    device_firmwares.sort(key=lambda item: item[1], reverse=True)
    return device_firmwares

def main():
    """Main function to run the firmware downloader tool."""
    FIRMWARE_URL = "https://deconz.dresden-elektronik.de/deconz-firmware/"
    
    DEVICES = {
        "1": {"name": "ConBee", "pattern": r"deCONZ_Rpi_0x"},
        "2": {"name": "ConBee II", "pattern": r"deCONZ_ConBeeII_0x"},
        "3": {"name": "ConBee III", "pattern": r"deCONZ_ConBeeIII_0x"},
        "4": {"name": "RaspBee", "pattern": r"deCONZ_Rpi_0x"},
        "5": {"name": "RaspBee II", "pattern": r"deCONZ_RaspBeeII_0x"},
    }

    print("--- deCONZ Firmware Downloader ---")

    save_directory = input("\nEnter the directory to save files to (press Enter for current folder): ").strip()
    if not save_directory:
        save_directory = '.'

    try:
        os.makedirs(save_directory, exist_ok=True)
        print(f"Files will be saved in: {os.path.abspath(save_directory)}")
    except OSError as e:
        print(f"Error: Could not create directory '{save_directory}'. Reason: {e}")
        return
    
    print("\n--- GCFFlasher Check ---")
    download_latest_gcfflasher(save_path=save_directory)
    
    print("\n------------------------------------")
    
    all_firmware_data = get_firmware_list_with_dates(FIRMWARE_URL)
    if not all_firmware_data:
        return

    print("\nPlease select your device to download firmware for:")
    for key, device in DEVICES.items():
        print(f"  {key}: {device['name']}")
    
    choice = input("\nEnter the number of your device: ")

    if choice not in DEVICES:
        print("Invalid selection. Please run the script again.")
        return

    selected_device = DEVICES[choice]
    print(f"\nSearching for available firmware for {selected_device['name']}...")
    
    available_versions = filter_and_sort_versions(all_firmware_data, selected_device['pattern'])

    if not available_versions:
        print(f"Could not find any firmware for {selected_device['name']}.")
        return

    print(f"\nFound {len(available_versions)} versions for {selected_device['name']}:")
    for i, (filename, date) in enumerate(available_versions):
        print(f"  {i+1}: {filename}  (Released: {date.strftime('%Y-%m-%d')})")
        
    try:
        selection = int(input("\nEnter the number of the version you want to download (or 0 to cancel): "))
        if selection == 0:
            print("Download cancelled.")
            return
        if 1 <= selection <= len(available_versions):
            selected_file = available_versions[selection - 1][0]
            download_file(f"{FIRMWARE_URL}{selected_file}", save_path=save_directory)
        else:
            print("Invalid selection.")
    except ValueError:
        print("Invalid input. Please enter a number.")

if __name__ == "__main__":
    main()

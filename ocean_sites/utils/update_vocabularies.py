import os
import requests
import smtplib

# Point to your Dash assets folder
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")

def download_vocabulary(url, filename):
    print(f"Downloading {filename}...")
    filepath = os.path.join(ASSETS_DIR, filename)
    tmp_filepath = filepath + ".tmp"
    
    try:
        os.makedirs(ASSETS_DIR, exist_ok=True)
        
        # 1. Download the data
        response = requests.get(url, timeout=15)
        response.raise_for_status() # Immediately aborts on a 404/500 error!
        
        # 2. Write to a TEMPORARY file first
        with open(tmp_filepath, 'wb') as f:
            f.write(response.content)
            
        # 3. Atomic replace: Safely overwrite the real file only if step 1 & 2 succeeded
        os.replace(tmp_filepath, filepath)
        print(f"✅ Successfully updated {filename}")
        
    except Exception as e:
        # If it failed, clean up the temp file if it exists
        if os.path.exists(tmp_filepath):
            os.remove(tmp_filepath)
            
        error_msg = f"❌ Failed to download {filename}: {e}"
        print(error_msg)

if __name__ == "__main__":
    print("Starting vocabulary updates...")
    
    # 1. CF Standard Names (XML)
    download_vocabulary(
        "https://cfconventions.org/Data/cf-standard-names/current/src/cf-standard-name-table.xml", 
        "cf-standard-name-table.xml"
    )
    
    # 2. SeaVoX C19 - Sea Area Names (JSON-LD)
    download_vocabulary(
        "https://vocab.nerc.ac.uk/collection/C19/current/?_profile=nvs&_mediatype=application/ld+json", 
        "seavox_C19.json"
    )
    
    # 3. SeaVoX L06 - Platform Categories (JSON-LD)
    download_vocabulary(
        "https://vocab.nerc.ac.uk/collection/L06/current/?_profile=nvs&_mediatype=application/ld+json", 
        "seavox_L06.json"
    )

    # 4. NDBC THREDDS Catalog (XML)
    download_vocabulary(
        "https://dods.ndbc.noaa.gov/thredds/catalog/data/oceansites/DATA/catalog.xml", 
        "thredds_catalog.xml"
    )
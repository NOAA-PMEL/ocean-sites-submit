import os
import requests
import json
import re
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
        response = requests.get(url, timeout=30)
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

def slim_seavox_l22(filename: str = "seavox_L22.json") -> None:
    """Extract active {L22_ID: prefLabel} pairs from the downloaded L22 JSON-LD file."""
    filepath = os.path.join(ASSETS_DIR, filename)
    if not os.path.exists(filepath):
        return

    tmp_filepath = filepath + ".tmp"
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        # Skip if already slimmed (e.g., {"TOOL0022": "...", "NETT0001": "..."})
        if "@graph" not in raw_data:
            return

        tools: dict[str, str] = {}
        for concept in raw_data.get("@graph", []):
            # Skip collection-level metadata node and deprecated concepts
            concept_type = concept.get("@type", "")
            if "skos:Collection" in concept_type:
                continue
            if concept.get("owl:deprecated") in (True, "true", "True"):
                continue

            # Extract local L22 ID (e.g., TOOL0022, NETT0001, CORE0005)
            code = None
            for field in ("skos:notation", "dc:identifier"):
                val = concept.get(field)
                if isinstance(val, dict):
                    val = val.get("@value")
                if isinstance(val, str) and val.strip():
                    code = val.strip().split("::")[-1]
                    break

            if not code and isinstance(concept.get("@id"), str):
                uri = concept["@id"].rstrip("/")
                if "/collection/L22/current/" in uri:
                    code = uri.rsplit("/", 1)[-1]

            if not code:
                continue

            label = concept.get("skos:prefLabel")
            if isinstance(label, dict):
                label = label.get("@value") if label.get("@language") == "en" else ""
            tools[code] = label.strip() if isinstance(label, str) else ""

        if not tools:
            raise ValueError("Extracted 0 active L22 IDs from SeaVoX L22 JSON-LD.")

        with open(tmp_filepath, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(tools.items())), f, indent=2, ensure_ascii=False)

        os.replace(tmp_filepath, filepath)
        print(f"✅ Extracted {len(tools)} active L22 IDs into {filename}")

    except Exception as e:
        if os.path.exists(tmp_filepath):
            os.remove(tmp_filepath)
        print(f"❌ Failed to extract L22 tool codes from {filename}: {e}")

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

    # 4. SeaVoX L22 - SeaVoX Device Catalogue (JSON-LD)
    download_vocabulary(
        "https://vocab.nerc.ac.uk/collection/L22/current/?_profile=nvs&_mediatype=application/ld+json",
        "seavox_L22.json"
    )
    slim_seavox_l22("seavox_L22.json")

    # 5. NDBC THREDDS Catalog (XML)
    download_vocabulary(
        "https://dods.ndbc.noaa.gov/thredds/catalog/data/oceansites/DATA/catalog.xml", 
        "thredds_catalog.xml"
    )
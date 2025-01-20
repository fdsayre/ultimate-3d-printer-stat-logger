from typing import List, Dict, Set, Optional, Any, Union
import requests
import csv
import logging
import time
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import pytz
from gspread.exceptions import APIError
import xml.etree.ElementTree as ET

# Configuration
CONFIG = {
    'SHEET_NAME': 'Makerspace 3D Printer Stats',
    'CREDENTIALS_PATH': '/opt/ulti/credentials.json',
    'MAX_RETRIES': 3,
    'RETRY_BACKOFF': 0.5,
    'REQUEST_TIMEOUT': 10,
    'BATCH_SIZE': 50
}

class PrinterAPI:
    def __init__(self, ip: str, session: requests.Session):
        self.ip = ip
        self.session = session
        self._name = None

    @property
    def name(self) -> str:
        if self._name is None:
            response = self.make_request('system')
            if isinstance(response, dict):
                self._name = response.get('name', f"Printer-{self.ip}")
            else:
                self._name = f"Printer-{self.ip}"
        return self._name

    def make_request(self, endpoint: str) -> dict:
        try:
            url = f"http://{self.ip}/api/v1/{endpoint}"
            response = self.session.get(url, timeout=CONFIG['REQUEST_TIMEOUT'])
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.warning(f"Request failed for {self.ip} at {endpoint}: {e}")
            return {}

    def get_material_name(self, material_guid: str) -> str:
        if not material_guid:
            return "Unknown"
        try:
            response = self.make_request(f"materials/{material_guid}")
            if isinstance(response, str):  # If the response is XML
                try:
                    root = ET.fromstring(response)
                    material_element = root.find(".//{http://www.ultimaker.com/material}material")
                    if material_element is not None:
                        return material_element.text.strip()
                except ET.ParseError:
                    logging.warning(f"Failed to parse material XML for GUID {material_guid}")
            return "Unknown"
        except requests.exceptions.RequestException:
            return "Unknown"

class UltimakerLogger:
    def __init__(self, ip_file: str, csv_path: str = 'print_logs.csv'):
        self.printer_ips = self._load_printer_ips(ip_file)
        self.csv_path = Path(csv_path)
        self.existing_uuids = set()
        self.session = self._setup_requests_session()
        self.printers = [PrinterAPI(ip, self.session) for ip in self.printer_ips]
        self._load_existing_uuids()

    def _load_printer_ips(self, ip_file: str) -> list:
        try:
            with open(ip_file, 'r') as f:
                return [line.strip() for line in f if line.strip() and not line.startswith('#')]
        except FileNotFoundError:
            logging.error(f"IP file '{ip_file}' not found. Exiting.")
            exit(1)

    def _setup_requests_session(self) -> requests.Session:
        session = requests.Session()
        retry_strategy = Retry(
            total=CONFIG['MAX_RETRIES'],
            backoff_factor=CONFIG['RETRY_BACKOFF'],
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _load_existing_uuids(self) -> None:
        try:
            if self.csv_path.exists():
                with self.csv_path.open('r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    self.existing_uuids = {row['uuid'] for row in reader if row['uuid']}
                logging.info(f"Loaded {len(self.existing_uuids)} existing UUIDs")
        except Exception as e:
            logging.error(f"Error loading existing UUIDs: {e}")
            self.existing_uuids = set()

    def collect_logs(self):
        all_print_jobs = []

        for printer in self.printers:
            logging.info(f"Connecting to printer: {printer.ip}")
            printer_name = printer.name
            logging.info(f"Printer name: {printer_name}")

            offset = 0
            while True:
                history = printer.make_request(f"history/print_jobs?offset={offset}&count={CONFIG['BATCH_SIZE']}")
                if not isinstance(history, list):
                    logging.warning(f"Unexpected response type from {printer.ip}: {type(history)}")
                    break

                for job in history:
                    if isinstance(job, dict):
                        processed_job = self._process_print_job(printer, job)
                        if processed_job and job.get('uuid') not in self.existing_uuids:
                            all_print_jobs.append(processed_job)
                    else:
                        logging.debug(f"Skipping non-dictionary job entry: {type(job)}")

                if len(history) < CONFIG['BATCH_SIZE']:
                    break
                offset += CONFIG['BATCH_SIZE']

        if all_print_jobs:
            self._save_jobs(all_print_jobs)

    def _process_print_job(self, printer: PrinterAPI, job: dict) -> Optional[dict]:
        """Process a single print job."""
        # Only process completed or aborted prints
        if job.get('result') not in ['Finished', 'Aborted']:
            return None

        # Ensure we have a dictionary
        if not isinstance(job, dict):
            logging.debug(f"Skipping non-dictionary job data: {type(job)}")
            return None

        try:
            # Get the date in YYYY-MM-DD format from datetime_started
            start_date = ''
            if job.get('datetime_started'):
                dt = datetime.fromisoformat(job.get('datetime_started', '').replace('Z', '+00:00'))
                start_date = dt.date().isoformat()

            return {
                'uuid': job.get('uuid', ''),
                'printer_name': printer.name,
                'date': start_date,                    
                'datetime_started': self._convert_to_pst(job.get('datetime_started', '')),
                'datetime_finished': self._convert_to_pst(job.get('datetime_finished', '')),
                'name': job.get('name', ''),
                'result': job.get('result', ''),
                'time_total': job.get('time_total', 0),
                'material_0_amount': max(0, job.get('material_0_amount', 0)),
                'material_1_amount': max(0, job.get('material_1_amount', 0)),
                'material_0_name': printer.get_material_name(job.get('material_0_guid', '')),
                'material_1_name': printer.get_material_name(job.get('material_1_guid', ''))
            }
        except Exception as e:
            logging.debug(f"Error processing job: {e}, Job data: {job}")
            return None

    def _save_jobs(self, print_jobs: list):
        fieldnames = [
            'uuid', 'printer_name', 'date', 'datetime_started', 'datetime_finished', 
            'name', 'result', 'time_total', 'material_0_amount', 
            'material_1_amount', 'material_0_name', 'material_1_name'
        ]

        try:
            # Save to CSV
            file_exists = self.csv_path.exists()
            with self.csv_path.open('a' if file_exists else 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if not file_exists:
                    writer.writeheader()
                writer.writerows(print_jobs)

            # Update Google Sheets
            self._update_google_sheets(print_jobs, fieldnames)
            
            logging.info(f"Successfully saved {len(print_jobs)} new print jobs")
            
        except Exception as e:
            logging.error(f"Error saving jobs: {e}")

    def _update_google_sheets(self, print_jobs: list, fieldnames: list, batch_size: int = 100):
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name(CONFIG['CREDENTIALS_PATH'], scope)
            client = gspread.authorize(creds)
            sheet = client.open(CONFIG['SHEET_NAME']).sheet1

            # Convert dictionaries to lists in the correct order
            rows = [[job[field] for field in fieldnames] for job in print_jobs]

            # Batch upload
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i + batch_size]
                sheet.append_rows(batch)
                if i + batch_size < len(rows):
                    time.sleep(1)  # Rate limiting

        except Exception as e:
            logging.error(f"Failed to update Google Sheets: {e}")

    @staticmethod
    def _convert_to_pst(iso_timestamp: str) -> str:
        if not iso_timestamp:
            return ""
        try:
            utc_time = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
            pst_timezone = pytz.timezone("America/Los_Angeles")
            return utc_time.astimezone(pst_timezone).isoformat()
        except Exception as e:
            logging.error(f"Error converting timestamp: {iso_timestamp}. Error: {e}")
            return iso_timestamp

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    ip_file = 'printer_ips.txt'
    csv_path = 'ultimaker_logs.csv'

    logger = UltimakerLogger(ip_file=ip_file, csv_path=csv_path)
    logger.collect_logs()

if __name__ == "__main__":
    main()

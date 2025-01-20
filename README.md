# Ultimaker Print Statistics Logger

A Python script to automatically collect print statistics from Ultimaker 3D printers on your local network using the built-in API and store them in both a CSV file and in Google Sheets.

## Background

This script was developed to address changes over the past several years that moved detailed printer statistics behind a subscription wall. These changes include removing all print length and time statistics from the local copy of print history files except for the most recent prints. This is particularly annoying because there is no technical reason for this. The only possible reason is as a further way of making people subscribe to their online product, which we otherwise will never need. This script uses the printers' local API to collect essential usage data without requiring a subscription.

I built this over about 10 hours using ChatGPT and Claude. I have minimal programming experience (but I do have some).  It's running as a systemd timer on a Raspberry Pi in our print room.

## Features

* Collects print statistics from multiple Ultimaker printers on your network
* Stores data in both a local CSV file and in Google Sheets
* Tracks print time and material use
* Only records prints that have completed (including aborted prints)
* Converts timestamps to local timezone (PST)
* Avoids duplicate entries using UUIDs

## How It Works

The script performs the following steps:

1. Reads printer IP addresses from a configuration file
2. Connects to each printer's local API
3. Retrieves the last batch of print jobs from each printer
4. Processes job data including:
   - Print duration
   - Material usage
   - Start/end times
   - Job status
   - Material types used
5. Converts timestamps to local timezone
6. Saves new entries to a local CSV file
7. Updates a Google Sheet with the new data

## Setup

1. Clone this repository
2. Install required packages
3. Create a `printer_ips.txt` file with your printer IP addresses
4. Set up Google Sheets API credentials and save as `credentials.json`
5. Update `CONFIG` settings in the script if needed
6. Run the script: `python ultimaker_logger.py`

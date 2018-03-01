#!/usr/bin/env python3
import argparse as ap
import base64
import datetime
import getpass
import hashlib
import json
import os.path
import platform
import re
import sys
import time
import urllib
import yaml

from dateutil import parser as dateparser
from dateutil import tz
from os.path import expanduser
from os.path import splitext
from requests_wrapper import requests_wrapper as requests

# Default values
application_version = "0.1.26"
config_file = "config.yml"
verbose = False
data = {
    "last_login": None,
    "parameters_file": None,
    "server": {
        "port": "8200",
        "protocol": "http",
        "url": "localhost",
    },
    'token': None,
    'token_expires': None,
    'verbose': False
}


def main(**args):
    # Command method
    method = sys.argv[1]

    # Default values
    global verbose
    global config_file
    global data

    # Detect home dir for config file
    config_file = get_config_location()

    # Use an alternative config file if --config-file is provided
    if args.get("config_file", False):
        config_file = args["config_file"]

    # Load configuration
    overwrite = args.get("overwrite", False)
    data = load_config(data, overwrite)

    param_file = args.get("param-file", None)
    # Set parameters file
    if method == "params":
        data = set_parameters_file(data, args, param_file)

    # Load parameters file
    args = load_parameters(data, args)

    # Show parameters
    if method == "params" and (args.get("show", False) or param_file is None):
        display_parameters(data)

    # Toggle verbosity
    if method == "verbose":
        toggle_verbose(data)

    # Write verbosity setting to global variable
    verbose = data.get("verbose", False)

    # Display the config if requested
    if method == "config":
        display_config(data)

    # Display the status if requested
    if method == "status":
        display_status(data)

    # Login
    if method == "login":
        url = args.get("url", None)
        password = args.get("password", None)
        data = login(data, url, password)

    # Logout
    if method == "logout":
        data = logout(data)

    # List resources
    if method == "list":
        resource_type = args.get("type", None)
        list_resources(data, resource_type)

    # Get resources
    if method == "get":
        resource_type = args.get("type", None)
        resource_id = args.get("id", None)
        get_resources(data, resource_type, resource_id)

    # Get resources
    if method == "describe":
        resource_type = args.get("type", None)
        resource_id = args.get("id", None)
        describe_resource(data, resource_type, resource_id)

    # Show logs
    if method == "logs":
        log_type = args.get("type", None)
        backup_id = args.get("id", None)
        remote = args.get("remote", False)
        follow = args.get("follow", False)
        lines = args.get("lines", 10)
        show_all = args.get("all", False)
        get_logs(data, log_type, backup_id, remote, follow, lines, show_all)

    # Run backup
    if method == "run":
        backup_id = args.get("id", None)
        run_backup(data, backup_id)

    # Abort backup
    if method == "abort":
        backup_id = args.get("id", None)
        abort_task(data, backup_id)

    # Delete a backup
    if method == "delete":
        backup_id = args.get("id", None)
        delete_db = args.get("delete_db", False)
        confirm = args.get("confirm", False)
        delete_backup(data, backup_id, delete_db, confirm)

    # Import method
    if method == "import":
        import_type = args.get("type", None)
        import_file = args.get("import-file", None)
        import_id = args.get("id", None)
        import_meta = args.get("import_metadata", None)
        strip_meta = args.get("strip_metadata", False)
        # Strip meta data is only valid when import_id is specified
        if import_id is not None and not strip_meta:
            import_meta = True

        import_resource(data, import_type, import_file, import_id, import_meta)

    # Export method
    if method == "export":
        resource_type = args.get("type", None)
        resource_id = args.get("id", None)
        output_type = args.get("output", None)
        path = args.get("output_path", None)
        export_resource(data, resource_type, resource_id, output_type, path)


# Function for display a list of resources
def list_resources(data, resource):
    verify_token(data)

    if resource == "backups":
        resource_list = fetch_backup_list(data)
    else:
        resource_list = fetch_resource_list(data, resource)

    resource_list = list_filter(resource_list, resource)

    if len(resource_list) == 0:
        log_output("No items found", True)
        sys.exit(2)

    # Must use safe_dump for python 2 compatibility
    message = yaml.safe_dump(resource_list, default_flow_style=False)
    log_output(message, True, 200)


# Fetch all backups
def fetch_backup_list(data):
    backups = fetch_resource_list(data, "backups")

    # Fetch progress state
    progress_state, active_id = fetch_progress_state(data)

    backup_list = []
    for backup in backups:
        backup_id = backup.get("Backup", {}).get("ID", 0)
        if active_id is not None and backup_id == active_id:
            backup["Progress"] = progress_state
        backup_list.append(backup)

    return backup_list


# Fetch all resources of a certain type
def fetch_resource_list(data, resource):
    baseurl = create_baseurl(data, "/api/v1/")
    log_output("Fetching " + resource + " list from API...", False)
    cookies = create_cookies(data)
    headers = create_headers(data)
    r = requests.get(baseurl + resource, headers=headers, cookies=cookies)
    check_response(data, r.status_code)
    if r.status_code != 200:
        log_output("Error connecting", True, r.status_code)
        sys.exit(2)
    else:
        return r.json()


# Filter logic for the list function to facilitate readable output
def list_filter(json_input, resource):
    resource_list = []
    if resource == "backups":
        for key in json_input:
            backup = key.get("Backup", None)
            schedule = key.get("Schedule", None)
            progress_state = key.get("Progress", None)
            backup_name = backup.get("Name", "")
            backup = {
                backup_name: {
                    "ID": backup.get("ID", ""),
                }
            }

            if backup.get('Metadata', {}).get('SourceSizeString') is not None:
                size = backup.get('Metadata', {}).get('SourceSizeString')
                backup[backup_name]["Source size"] = size

            if schedule is not None:
                next_run = format_time(schedule.get("Time", ""))
                if next_run is not None:
                    backup[backup_name]["Next run"] = next_run

                last_run = format_time(schedule.get("LastRun", ""))
                if last_run is not None:
                    backup[backup_name]["Last run"] = last_run

            if progress_state is not None:
                backup[backup_name]["Running"] = {
                    "Task ID": progress_state.get("TaskID", None),
                    "State": progress_state.get("Phase", None),
                }

            resource_list.append(backup)

    elif resource == "notifications":
        for val in json_input:
            notification = {
                val.get("Title", ""): {
                    "Backup ID": val.get("BackupID", ""),
                    "Notification ID": val.get("ID", ""),
                }
            }
            timestamp = format_time(val.get("Timestamp", ""))
            if timestamp is not None:
                notification["Timestamp"] = timestamp

            resource_list.append(notification)

    elif resource == "serversettings":
        for key, value in json_input.items():
            if key == "update-check-latest":
                continue
            setting = {
                key: {
                    "value": value
                }
            }

            resource_list.append(setting)
    else:
        resource_list = json_input

    return resource_list


# Get one or more resources with somewhat limited fields
def get_resources(data, resource_type, resource_id):
    if resource_type == "backup":
        result = fetch_backups(data, resource_id, "get")
    elif resource_type == "notification":
        result = fetch_notifications(data, resource_id, "get")

    message = yaml.safe_dump(result, default_flow_style=False)
    log_output(message, True, 200)


# Get one resource with all fields
def describe_resource(data, resource_type, resource_id):
    if resource_type == "backup":
        result = fetch_backups(data, [resource_id], "describe")
        # Must use safe_dump for python 2 compatibility
    elif resource_type == "notification":
        result = fetch_notifications(data, [resource_id], "describe")

    message = yaml.safe_dump(result, default_flow_style=False)
    log_output(message, True, 200)


# Fetch notifications
def fetch_notifications(data, notification_ids, method):
    verify_token(data)

    log_output("Fetching notifications from API...", False)
    baseurl = create_baseurl(data, "/api/v1/notifications")
    cookies = create_cookies(data)
    headers = create_headers(data)
    notification_list = []
    r = requests.get(baseurl, headers=headers, cookies=cookies)
    check_response(data, r.status_code)
    if r.status_code != 200:
        id_list = ', '.join(notification_ids)
        message = "Error getting notifications " + id_list
        log_output(message, True, r.status_code)
    else:
        data = r.json()

    for notification in data:
        notification_id = notification.get("ID", -1)
        if str(notification_id) in notification_ids:
            notification_list.append(notification)

    # Only get uses a filter
    if method == "get":
        notification_list = notification_filter(notification_list)

    return notification_list


# Filter logic for the notification get command
def notification_filter(json_input):
    notification_list = []
    for key in json_input:
        title = key.get("Title", "Notification")
        notification = {
            title: {
                "Backup ID": key.get("BackupID", ""),
                "Notification ID": key.get("ID", ""),
                "Message": key.get("Message", ""),
                "Type": key.get("Type", ""),
            }
        }
        timestamp = format_time(key.get("Timestamp", ""))
        if timestamp is not None:
            notification[title]["Timestamp"] = timestamp

        notification_list.append(notification)

    return notification_list


# Fetch backups
def fetch_backups(data, backup_ids, method):
    verify_token(data)

    log_output("Fetching backups from API...", False)
    progress_state, active_id = fetch_progress_state(data)
    backup_list = []
    baseurl = create_baseurl(data, "/api/v1/backup/")
    cookies = create_cookies(data)
    headers = create_headers(data)
    # Iterate over backup_ids and fetch their info
    for backup_id in backup_ids:
        r = requests.get(baseurl + str(backup_id),
                         headers=headers, cookies=cookies)
        check_response(data, r.status_code)
        if r.status_code != 200:
            message = "Error getting backup " + str(backup_id)
            log_output(message, True, r.status_code)
            continue
        data = r.json()["data"]

        item_id = data.get("Backup", {}).get("ID", 0)
        if active_id is not None and item_id == active_id:
            data["Progress"] = progress_state
        backup_list.append(data)

    if len(backup_list) == 0:
        sys.exit(2)

    # Only get uses a filter
    if method == "get":
        backup_list = backup_filter(backup_list)

    return backup_list


# Fetch backup progress state
def fetch_progress_state(data):
    baseurl = create_baseurl(data, "/api/v1/progressstate")
    cookies = create_cookies(data)
    headers = create_headers(data)
    # Check progress state and get info for the running backup
    r = requests.get(baseurl, headers=headers, cookies=cookies)
    if r.status_code != 200:
        log_output("Error getting progressstate ", False, r.status_code)
        active_id = -1
        progress_state = {}
    else:
        progress_state = r.json()
        active_id = progress_state.get("BackupID", -1)

    return progress_state, active_id


# Filter logic for the fetch backup/backups methods
def backup_filter(json_input):
    backup_list = []
    for key in json_input:
        backup = key.pop("Backup", {})
        metadata = backup.pop("Metadata", {})
        backup_name = backup.pop("Name", {})
        backup = {
            "ID": backup.get("ID", ""),
            "Local database": backup.get("DBPath", ""),
        }
        backup["Versions"] = int(metadata.get("BackupListCount", 0))
        backup["Last run"] = {
            "Duration": format_duration(metadata.get("LastDuration", "0")),
            "Started": format_time(metadata.get("LastStarted", "0")),
            "Stopped": format_time(metadata.get("LastFinished", "0")),
        }
        backup["Size"] = {
            "Local": metadata.get("SourceSizeString", ""),
            "Backend": metadata.get("TargetSizeString", "")
        }

        schedule = key.get("Schedule", None)
        if schedule is not None:
            next_run = format_time(schedule.pop("Time", ""))
            if next_run is not None:
                schedule["Next run"] = next_run
            last_run = format_time(schedule.pop("LastRun", ""))
            if last_run is not None:
                schedule["Last run"] = last_run
            schedule.pop("AllowedDays", None)
            schedule.pop("ID", None)
            schedule.pop("Rule", None)
            schedule.pop("Tags", None)

        progress_state = key.get("Progress", {})
        state = progress_state.get("Phase", None)
        speed = progress_state.get("BackendSpeed", 0)
        progress = {
            "State": state,
            "Counting": progress_state.get("StillCounting", False),
            "Backend": {
                "Action": progress_state.get("BackendAction", 0)
            },
            "Task ID": progress_state.get("TaskID", -1),
        }
        if speed > 0:
            progress["Backend"]["Speed"] = bytes_2_human_readable(speed) + "/s"

        # Display item only if relevant
        if not progress_state.get("StillCounting", False):
            progress.pop("Counting")
        # Avoid 0 division
        file_count = progress_state.get("ProcessedFileCount", 0)
        total_file_count = progress_state.get("TotalFileCount", 0)
        processing = state == "Backup_ProcessingFiles"
        if file_count > 0 and total_file_count > 0 and processing:
            processed = "{0:.2f}".format(file_count / total_file_count * 100)
            progress["Processed files"] = processed + "%"
        # Avoid 0 division
        current = progress_state.get("BackendFileProgress", 0)
        total = progress_state.get("BackendFileSize", 0)
        if current > 0 and total > 0:
            backend_progress = "{0:.2f}".format(current / total * 100)
            progress["Backend"]["Progress"] = backend_progress + "%"
        # Don't show the backend stats on finished tasks
        phase = progress_state.get("Phase", "")
        if phase in ["Backup_Complete", "Error"]:
            progress.pop("Backend")

        backup["Schedule"] = schedule
        backup["Progress"] = progress
        key = {
            backup_name: backup
        }
        backup_list.append(key)

    return backup_list


# Fetch logs
def get_logs(data, log_type, backup_id, remote=False,
             follow=False, lines=10, show_all=False):
        verify_token(data)

        if log_type == "backup" and backup_id is None:
            log_output("A backup id must be provided with --id", True)
            sys.exit(2)

        # Treating functions as objects to allow any function to be "followed"
        if log_type == "backup" and remote:
            def function():
                get_backup_logs(data, backup_id, "remotelog", lines, show_all)
        elif log_type == "backup" and not remote:
            def function():
                get_backup_logs(data, backup_id, "log", lines, show_all)
        elif log_type in ["profiling", "information", "warning", "error"]:
            def function():
                get_live_logs(data, log_type, lines)
        elif log_type == "stored":
            def function():
                get_stored_logs(data, lines, show_all)

        # Follow the function or just run it once
        if follow:
            follow_function(function, 10)
        else:
            function()


# Get local and remote backup logs
def get_backup_logs(data, backup_id, log_type, page_size=5, show_all=False):
    endpoint = "/api/v1/backup/" + str(backup_id) + "/" + log_type
    baseurl = create_baseurl(data, endpoint)
    cookies = create_cookies(data)
    headers = create_headers(data)
    params = {'pagesize': page_size}

    r = requests.get(baseurl, headers=headers, cookies=cookies, params=params)
    check_response(data, r.status_code)
    if r.status_code == 500:
        log_output("Error getting log, database may be locked by backup", True)
        return
    elif r.status_code != 200:
        log_output("Error getting log", True, r.status_code)
        return

    result = r.json()[-page_size:]
    logs = []
    for log in result:
        if log.get("Operation", "") == "list":
            log["Data"] = "Expunged"
        else:
            log["Data"] = json.loads(log.get("Data", "{}"))
            size = bytes_2_human_readable(log["Data"].get("Size", 0))
            log["Data"]["Size"] = size

        if log.get("Message", None) is not None:
            log["Message"] = log["Message"].split("\n")
            message_length = len(log["Message"])
            if message_length > 15 and not show_all:
                log["Message"] = log["Message"][:15]
                lines = str(message_length - 15)
                hidden_message = lines + " hidden lines (show with --all)"
                log["Message"].append(hidden_message)
        if log.get("Exception", None) is not None:
            log["Exception"] = log["Exception"].split("\n")
            exception_length = len(log["Exception"])
            if exception_length > 15 and not show_all:
                log["Exception"] = log["Exception"][:15]
                lines = str(exception_length - 15)
                hidden_message = lines + " hidden lines (show with --all)"
                log["Exception"].append(hidden_message)

        log["Timestamp"] = datetime.datetime.fromtimestamp(
            int(log.get("Timestamp", 0))
        ).strftime("%I:%M:%S %p %d/%m/%Y")
        logs.append(log)
    message = yaml.safe_dump(logs, default_flow_style=False)
    log_output(message, True)


# Get live logs
def get_live_logs(data, level, page_size=5, first_id=0):
    baseurl = create_baseurl(data, "/api/v1/logdata/poll")
    cookies = create_cookies(data)
    headers = create_headers(data)
    params = {'level': level, 'id': first_id, 'pagesize': page_size}

    r = requests.get(baseurl, headers=headers, cookies=cookies, params=params)
    check_response(data, r.status_code)
    if r.status_code == 500:
        log_output("Error getting log, database may be locked by backup", True)
        return
    elif r.status_code != 200:
        log_output("Error getting log", True, r.status_code)
        return

    result = r.json()[-page_size:]
    logs = []
    for log in result:
        log["When"] = format_time(log.get("When", ""), True)
        logs.append(log)

    if len(logs) == 0:
        log_output("No log entries found", True)
        return

    message = yaml.safe_dump(logs, default_flow_style=False)
    log_output(message, True)


# Get stored logs
def get_stored_logs(data, page_size=5, show_all=False):
    baseurl = create_baseurl(data, "/api/v1/logdata/log")
    cookies = create_cookies(data)
    headers = create_headers(data)
    params = {'pagesize': page_size}

    r = requests.get(baseurl, headers=headers, cookies=cookies, params=params)
    check_response(data, r.status_code)
    if r.status_code == 500:
        log_output("Error getting log, database may be locked by backup", True)
        return
    elif r.status_code != 200:
        log_output("Error getting log", True, r.status_code)
        return

    result = r.json()[-page_size:]
    logs = []
    for log in result:
        if log.get("Message", None) is not None:
            log["Message"] = log["Message"].split("\n")
            message_length = len(log["Message"])
            if message_length > 15 and not show_all:
                log["Message"] = log["Message"][:15]
                lines = str(message_length - 15)
                hidden_message = lines + " hidden lines (show with --all)"
                log["Message"].append(hidden_message)
        if log.get("Exception", None) is not None:
            log["Exception"] = log["Exception"].split("\n")
            exception_length = len(log["Exception"])
            if exception_length > 15 and not show_all:
                log["Exception"] = log["Exception"][:15]
                lines = str(exception_length - 15)
                hidden_message = lines + " hidden lines (show with --all)"
                log["Exception"].append(hidden_message)
        logs.append(log)

    if len(logs) == 0:
        log_output("No log entries found", True)
        return

    message = yaml.safe_dump(logs, default_flow_style=False)
    log_output(message, True)


# Repeatedly call other functions until interrupted
def follow_function(function, interval=5):
    try:
        while True:
            clear_prompt()
            function()
            log_output(format_time(datetime.datetime.now(), True), True)
            log_output("Press control+C to quit", True)
            time.sleep(interval)
    except KeyboardInterrupt:
        return


# Call the API to schedule a backup run next
def run_backup(data, backup_id):
    verify_token(data)

    baseurl = create_baseurl(data, "/api/v1/backup/" + str(backup_id) + "/run")
    cookies = create_cookies(data)
    headers = create_headers(data)

    r = requests.post(baseurl, headers=headers, cookies=cookies)
    check_response(data, r.status_code)
    if r.status_code != 200:
        log_output("Error scheduling backup ", True, r.status_code)
        return
    log_output("Backup scheduled", True, 200)


# Call the API to abort a task
def abort_task(data, task_id):
    verify_token(data)

    baseurl = create_baseurl(data, "/api/v1/task/" + str(task_id) + "/abort")
    cookies = create_cookies(data)
    headers = create_headers(data)

    r = requests.post(baseurl, headers=headers, cookies=cookies)
    check_response(data, r.status_code)
    if r.status_code != 200:
        log_output("Error aborting task ", True, r.status_code)
        return
    log_output("Task aborted", True, 200)


# Call the API to delete a backup
def delete_backup(data, backup_id, delete_db=False, confirm=False):
    verify_token(data)

    # Check if the backup exists
    result = fetch_backups(data, [backup_id], "get")
    if result is None or len(result) == 0:
        return

    if not confirm:
        # Confirm deletion with user
        name = next(iter(result[0]))
        agree = input('Delete "' + name + '"? (ID:' + str(backup_id) + ') [y/N]:')
        if agree not in ["Y", "y", "yes", "YES"]:
            log_output("Backup not deleted", True)
            return

    baseurl = create_baseurl(data, "/api/v1/backup/" + str(backup_id))
    cookies = create_cookies(data)
    headers = create_headers(data)
    # We cannot delete remote files because the captcha is graphical
    payload = {'delete-local-db': delete_db, 'delete-remote-files': False}

    r = requests.delete(baseurl, headers=headers,
                        cookies=cookies, params=payload)
    check_response(data, r.status_code)
    if r.status_code != 200:
        log_output("Error deleting backup", True, r.status_code)
        return
    log_output("Backup deleted", True, 200)


def update_backup(data, backup_id, backup_config, import_meta=True):
    verify_token(data)

    # Strip metadata if requested
    if import_meta is not None and not import_meta:
        backup_config.get("Backup", {}).pop("Metadata", None)

    baseurl = create_baseurl(data, "/api/v1/backup/" + str(backup_id))
    cookies = create_cookies(data)
    headers = create_headers(data)
    payload = json.dumps(backup_config, default=str)
    r = requests.put(baseurl, headers=headers,
                     cookies=cookies, data=payload)
    check_response(data, r.status_code)
    if r.status_code == 404:
        log_output("Backup not found", True, r.status_code)
        return
    elif r.status_code != 200:
        log_output("Error updating backup", True, r.status_code)
        return
    log_output("Backup updated", True, 200)


# Login by authenticating against the Duplicati API and extracting a token
def login(data, input_url=None, password=None):
    # Split protocol, url, and port if port is provided as CLI argument
    if input_url is not None:
        # Begin parsing the input url
        input_url = input_url.replace("/", "").replace("_", "")
        count = input_url.count(":")
        protocol = ""
        url = ""
        port = ""
        if count == 2:
            protocol, url, port = input_url.split(":")
        elif count == 1 and input_url.index(":") < 6:
            protocol, url = input_url.split(":")
        elif count == 1:
            url, port = input_url.split(":")
        elif count == 0:
            url = input_url
        else:
            log_output("Invalid URL", True)
            sys.exit(2)

    # Strip nondigits
    port = ''.join(re.findall(r'\d+', port))

    # Default to config file values for any missing parameters
    if protocol is None or protocol.lower() not in ["http", "https"]:
        protocol = data["server"]["protocol"]
    if url is None or url == "":
        url = data["server"]["url"]
    if port is None or port == "":
        port = data["server"]["port"]

    # Update config
    data["server"]["protocol"] = protocol
    data["server"]["url"] = url
    data["server"]["port"] = port
    # Make the login attempt
    baseurl = create_baseurl(data, "")
    log_output("Connecting to " + baseurl + "...", False)
    r = requests.get(baseurl, allow_redirects=False)
    check_response(data, r.status_code)
    if r.status_code == 200:
        log_output("OK", False, r.status_code)
        token = unquote(r.cookies["xsrf-token"])
    elif r.status_code == 302:
        # Get password by prompting user if no password was given in-line
        if password is None:
            log_output("Authentication required", True, r.status_code)
            password = getpass.getpass('Password:')

        log_output("Getting nonce and salt...", False)
        baseurl = create_baseurl(data, "/login.cgi")
        payload = {'get-nonce': 1}
        r = requests.post(baseurl, data=payload)
        if r.status_code != 200:
            log_output("Error getting salt from server", True, r.status_code)
            return

        salt = r.json()["Salt"]
        data["nonce"] = unquote(r.json()["Nonce"])
        token = unquote(r.cookies["xsrf-token"])
        log_output("Hashing password...", False)
        salt_password = password.encode() + base64.b64decode(salt)
        saltedpwd = hashlib.sha256(salt_password).digest()
        nonce_password = base64.b64decode(data["nonce"]) + saltedpwd
        noncedpwd = hashlib.sha256(nonce_password).digest()

        log_output("Authenticating... ", False)
        payload = {
            "password": base64.b64encode(noncedpwd).decode('utf-8')
        }
        cookies = {
            "xsrf-token": token,
            "session-nonce": data.get("nonce", "")
        }
        r = requests.post(baseurl, data=payload, cookies=cookies)
        check_response(data, r.status_code)
        if r.status_code == 200:
            log_output("Connected", True, r.status_code)
            data["session-auth"] = unquote(r.cookies["session-auth"])
        else:
            message = "Error authenticating against the server"
            log_output(message, True, r.status_code)
            sys.exit(2)
    else:
        message = "Error connecting to server"
        log_output(message, True, r.status_code)
        return

    # Update the config file with provided values
    data["token"] = token
    expiration = datetime.datetime.now() + datetime.timedelta(0, 600)
    data["token_expires"] = expiration
    data["last_login"] = datetime.datetime.now()
    write_config(data)
    log_output("Login successful", True)
    return


# Logout by deleting the token from memory and disk
def logout(data):
    log_output("Logging out...", True)
    data["token"] = None
    write_config(data)
    return data


# Toggle verbosity
def toggle_verbose(data):
    data["verbose"] = not data.get("verbose", False)
    write_config(data)
    verbose = data.get("verbose", True)
    message = "verbose mode: " + str(verbose)
    log_output(message, True)


# Print the status to stdout
def display_status(data):
    global config_file
    global application_version

    log_output("Application version: " + application_version, True)

    message = "Config file: " + config_file
    log_output(message, True)

    if data.get("parameters_file", None) is not None:
        param_file = data.get("parameters_file", "")
        message = "Params file: " + param_file
        log_output(message, True)

    token = data.get("token", None)
    token_expires = data.get("token_expires", None)
    if token is None or token_expires is None:
        log_output("Not logged in", True)
        sys.exit(2)

    if data.get("last_login", None) is not None:
        last_login = data.get("last_login", "")
        message = "Logged in  : " + format_time(last_login)
        log_output(message, True)

    if token_expires is not None:
        message = "Expiration : " + format_time(token_expires)
        log_output(message, True)


# Load the configration from disk
def load_config(data, overwrite=False):
    global config_file
    # If the config file doesn't exist, create it
    if os.path.isfile(config_file) is False or overwrite is True:
        log_output("Creating config file", True)
        write_config(data)
    # Load the configuration from the config file
    with open(config_file, 'r') as file:
        try:
            data = yaml.safe_load(file)
            validate_config(data)
            return data
        except yaml.YAMLError as exc:
            log_output(exc, True)
            sys.exit(2)


# function for validating that required config fields are present
def validate_config(data):
    valid = True
    if "server" not in data:
        valid = False
    if "protocol" not in data.get("server", {}):
        valid = False
    if "url" not in data.get("server", {}):
        valid = False
    if "port" not in data.get("server", {}):
        valid = False
    if "token" not in data:
        valid = False
    if "token_expires" not in data:
        valid = False

    if not valid:
        message = "Configuration appears to be invalid. "
        message += "You can re-create it with --overwrite."
        log_output(message, True)
        sys.exit(2)


# Write config to file
def write_config(data):
    global config_file
    directory = os.path.dirname(config_file)
    if not os.path.exists(directory):
        message = "Created directory \"" + directory + "\""
        log_output(message, True)
        os.makedirs(directory)
    with open(config_file, 'w') as file:
        file.write(yaml.dump(data, default_flow_style=False))


# Print the config to stdout
def display_config(data):
    log_output(yaml.dump(data, default_flow_style=False), True)


# Set parameters file
def set_parameters_file(data, args, file=None):
    # Disable parameters file if requested
    if args.get("disable", False):
        data.pop("parameters_file", None)
        write_config(data)
        log_output("Disabling parameters-file", True)
        return data

    if file is None:
        return data

    data["parameters_file"] = file
    write_config(data)
    log_output("Setting parameters-file", True)
    return data


# Load parameters from file
def load_parameters(data, args):
    # Check for parameters file
    file = data.get("parameters_file", None)
    if file is None:
        return args

    # Don't load nonexisting files
    if os.path.isfile(file) is False:
        return args

    # Load the parameters from the file
    with open(file, 'r') as file_handle:
        try:
            parameters_file = yaml.safe_load(file_handle)
            parameters = len(parameters_file)
            message = "Loaded " + str(parameters) + " parameters from file"
            log_output(message, True)

            for key, value in parameters_file.items():
                # Make sure not to override CLI provided arguments
                if args.get(key, None) is None:
                    args[key] = value

            # Verbose is special because verbose is a command not an argument
            if parameters_file.get("verbose", None) is not None:
                data["verbose"] = parameters_file.get("verbose")

            # Update parameters_file variable in config file
            data["parameters_file"] = file
            write_config(data)
            return args
        except yaml.YAMLError as exc:
            log_output(exc, True)
            return args


# Print parameters to stdout
def display_parameters(data):
    file = data.get("parameters_file", None)
    if file is None:
        return
    with open(file, 'r') as file_handle:
        try:
            parameters_file = yaml.safe_load(file_handle)
            output = yaml.dump(parameters_file, default_flow_style=False)
            log_output(output, True)
            return
        except Exception:
            message = "Could not load parameters file"
            log_output(message, True)
            return


# Import resource wrapper function
def import_resource(data, resource, import_file, backup_id, import_meta=None):
    if resource == "backup":
        import_backup(data, import_file, backup_id, import_meta)


# Import backup configuration from a YAML or JSON file
def import_backup(data, import_file, backup_id=None, import_meta=None):
    # Don't load nonexisting files
    if os.path.isfile(import_file) is False:
        log_output(import_file + " not found", True)
        return

    # Load the import file
    with open(import_file, 'r') as file_handle:
        extension = splitext(import_file)[1]
        if extension.lower() in ['.yml', '.yaml']:
            try:
                backup_config = yaml.safe_load(file_handle)
            except yaml.YAMLError:
                log_output("Failed to load file as YAML", True)
                return

        elif extension.lower() == ".json":
            try:
                backup_config = json.load(file_handle)
            except Exception:
                log_output("Failed to load file as JSON", True)
                return

    # Determine if we're importing a new backup or updating an existing backup
    if backup_id is not None:
        return update_backup(data, backup_id, backup_config, import_meta)

    verify_token(data)

    # Strip metadata if requsted
    if import_meta is None or import_meta is not True:
        backup_config["Backup"]["Metadata"] = {}

    # Prepare the imported JSON object as a string
    backup_config = json.dumps(backup_config, default=str)

    # Upload our JSON string as a file with requests
    files = {
        'config': ('backup_config.json', backup_config, 'application/json')
    }

    # Will eventually support passphrase encrypted configs, but we will
    # need to decrypt them in the client in order to convert them
    payload = {
        'passphrase': '',
        'import_metadata': import_meta,
        'direct': True
    }
    cookies = create_cookies(data)
    baseurl = create_baseurl(data, "/api/v1/backups/import", True)
    r = requests.post(baseurl, files=files, cookies=cookies, data=payload)
    check_response(data, r.status_code)
    if r.status_code != 200:
        log_output("Error importing backup configuration", True, r.status_code)
        sys.exit(2)
    log_output("Backup job created", True, 200)


# Export resource wrapper function
def export_resource(data, resource, resource_id, output=None, path=None):
    if resource == "backup":
        export_backup(data, resource_id, output, path)


# Export backup configuration to either YAML or JSON file
def export_backup(data, backup_id, output=None, path=None):
    # Get backup config
    result = fetch_backups(data, [backup_id], "describe")
    if result is None or len(result) == 0:
        log_output("Could not fetch backup", True)
        return
    backup = result[0]
    # Strip DisplayNames and Progress
    # backup.pop("DisplayNames", None)
    backup.pop("Progress", None)

    # Fetch server version
    systeminfo = fetch_resource_list(data, "systeminfo")

    if systeminfo.get("ServerVersion", None) is None:
        log_output("Error exporting backup", True)
        sys.exit(2)

    backup["CreatedByVersion"] = systeminfo["ServerVersion"]

    # YAML or JSON?
    if output in ["JSON", "json"]:
        filetype = ".json"
    else:
        filetype = ".yml"

    # Decide on where to output file
    if path is None:
        time = datetime.datetime.now().strftime("%d.%m.%Y_%I.%M_%p")
        path = "backup_config_" + str(time) + filetype
    else:
        path = expanduser(path)

    # Check if output folder exists
    directory = os.path.dirname(path)
    if directory != '' and not os.path.exists(directory):
        message = "Created directory \"" + directory + "\""
        log_output(message, True)
        os.makedirs(directory)
    # Check if output file exists
    if os.path.isfile(path) is True:
        agree = input('File already exists, overwrite? [Y/n]:')
        if agree not in ["Y", "y", "yes", "YES", ""]:
            return
    with open(path, 'w') as file:
        if filetype == ".json":
            file.write(json.dumps(backup, indent=4, default=str))
        else:
            file.write(yaml.dump(backup, default_flow_style=False))
    log_output("Created " + path, True, 200)


# Common function for verifying token validity
def verify_token(data):
    token = data.get("token", None)
    expires = data.get("token_expires", None)
    if token is None or expires is None:
        log_output("Not logged in", True)
        sys.exit(2)

    # Get time
    now = datetime.datetime.now()

    # Take care of timezones
    now = now.replace(tzinfo=tz.tzutc())
    now = now.astimezone(tz.tzlocal())
    expires = expires.replace(tzinfo=tz.tzutc())
    expires = expires.astimezone(tz.tzlocal())

    # Check if token has expired
    if now > expires:
        log_output("Token expired", True)
        sys.exit(2)


# Common function for checking API responses for session expiration
def check_response(data, status_code):
    # Exit if session expired
    if status_code == 400:
        message = "Session expired. Please login again"
        log_output(message, True, status_code)
        sys.exit(2)

    if status_code == 503:
        message = "Server is not responding. Is it running?"
        log_output(message, True, status_code)
        sys.exit(2)

    # Refresh token duration if request is OK
    if status_code == 200:
        expiration = datetime.datetime.now() + datetime.timedelta(0, 600)
        data["token_expires"] = expiration
        write_config(data)


# Common function for logging messages
def log_output(text, important, code=None):
    global verbose

    # Determine whether the message should be displayed in stdout
    if verbose is False and important is False:
        return
    if code is None or verbose is False:
        print(text)
        return

    print(text + "\nCode: " + str(code))


# Common function for creating cookies to authenticate against the API
def create_cookies(data):
    token = data.get("token", "")
    if data.get("nonce", None) is None:
        return {
            "xsrf-token": token
        }
    else:
        nonce = data.get("nonce", "")
        session_auth = data.get("session-auth", "")
        return {
            "xsrf-token": token,
            "session-nonce": nonce,
            "session-auth": session_auth
        }


# Common function for creating headers to authenticate against the API
def create_headers(data):
    return {
        "X-XSRF-TOKEN": data.get("token", "")
    }


# Common function for creating a base url
def create_baseurl(data, additional_path, append_token=False):
    protocol = data["server"]["protocol"]
    url = data["server"]["url"]
    port = data["server"]["port"]
    baseurl = protocol + "://" + url + ":" + port + additional_path
    if append_token is True:
        baseurl += "?x-xsrf-token=" + quote(data.get("token", ''))

    return baseurl


# Common function for formatting timestamps for humans
def format_time(time_string, precise=False):
    # Ensure it's a string
    time_string = str(time_string)

    # Filter out "unset" time
    if time_string == "0001-01-01T00:00:00Z" or time_string == "0":
        return None

    # We want to fail silently if we're not provided a parsable time_string.
    try:
        datetime_object = dateparser.parse(time_string)
    except Exception as exc:
        log_output(exc, False)
        return None

    # Print a precise, but human readable string if precise is true
    if precise:
        return datetime_object.strftime("%I:%M:%S %p %d/%m/%Y")

    # Now for comparison
    now = datetime.datetime.now()

    # Take care of timezones
    now = now.replace(tzinfo=tz.tzutc())
    now = now.astimezone(tz.tzlocal())
    datetime_object = datetime_object.replace(tzinfo=tz.tzutc())
    datetime_object = datetime_object.astimezone(tz.tzlocal())

    # Get the delta
    if (datetime_object > now):
        delta = (datetime_object - now)
    else:
        delta = (now - datetime_object)

    # Display hours if within 24 hours of now, else display dmy
    if abs(delta.days) > 1:
        return datetime_object.strftime("%d/%m/%Y")
    elif delta.days == 1:
        return "Yesterday " + datetime_object.strftime("%I:%M %p")
    elif delta.days == -1:
        return "Tomorrow " + datetime_object.strftime("%I:%M %p")
    else:
        return datetime_object.strftime("%I:%M %p")


# Common function for formatting time deltas for humans
def format_duration(duration_string):
    duration = duration_string.split(".")[0]
    return duration


# Common function for human readable bit sizes
# Source https://stackoverflow.com/questions/12523586/
def bytes_2_human_readable(number_of_bytes):
    if number_of_bytes < 0:
        raise ValueError("!!! numberOfBytes can't be smaller than 0 !!!")

    step_to_greater_unit = 1024.

    number_of_bytes = float(number_of_bytes)
    unit = 'bytes'

    if (number_of_bytes / step_to_greater_unit) >= 1:
        number_of_bytes /= step_to_greater_unit
        unit = 'KB'

    if (number_of_bytes / step_to_greater_unit) >= 1:
        number_of_bytes /= step_to_greater_unit
        unit = 'MB'

    if (number_of_bytes / step_to_greater_unit) >= 1:
        number_of_bytes /= step_to_greater_unit
        unit = 'GB'

    if (number_of_bytes / step_to_greater_unit) >= 1:
        number_of_bytes /= step_to_greater_unit
        unit = 'TB'

    precision = 2
    number_of_bytes = round(number_of_bytes, precision)

    return str(number_of_bytes) + ' ' + unit


# Use the correct directory for each OS
def get_config_location():
    home = expanduser("~")
    if platform.system() == 'Windows':
        config_dir = "/AppData/Local/DuplicatiClient/"
    else:
        config_dir = "/.config/duplicati-client/"

    config_file = home + config_dir + "/config.yml"
    return config_file


# Clear terminal prompt
def clear_prompt():
    if platform.system() == 'Windows':
        os.system('cls')
    else:
        os.system('clear')


# Python 3 vs 2 urllib compatibility issues
def unquote(text):
    if sys.version_info[0] >= 3:
        return urllib.parse.unquote(text)
    else:
        return urllib.unquote(text)


# More urllib compatibility issues
def quote(text):
    if sys.version_info[0] >= 3:
        return urllib.parse.quote_plus(text)
    else:
        return urllib.quote_plus(text)


# Client intro
def info():
    return """Duplicati Client

Connect to Duplicati remotely or locally and manage them through the CLI.

To begin log into a server:
    duplicati login https://my.duplicati.server

or see --help to see information on usage
"""


# argparse argument logic
if __name__ == '__main__':
    if (len(sys.argv) == 1):
        log_output(info(), True)
        sys.exit(2)
    # Initialize argument parser and standard optional arguments
    parser = ap.ArgumentParser()

    # Create subparsers
    subparsers = parser.add_subparsers(title='commands', metavar="<>", help="")

    # Subparser for the List method
    message = "List all resources of a given type"
    list_parser = subparsers.add_parser('list', help=message)
    choices = [
        "backups",
        "restores",
        "notifications",
        "serversettings",
        "systeminfo"
    ]
    message = "the type of resource"
    list_parser.add_argument('type', choices=choices, help=message)

    # Subparser for the Get method
    message = "display breif information on one or many resources"
    get_parser = subparsers.add_parser('get', help=message)
    message = "the type of resource"
    choices = ["backup", "notification"]
    get_parser.add_argument('type', choices=choices, help=message)
    message = "one or more ID's to look up"
    get_parser.add_argument('id', nargs='+', help=message)

    # Subparser for the Describe method
    message = "display detailed information on a specific resource"
    describe_parser = subparsers.add_parser('describe', help=message)
    message = "the type of resource"
    choices = [
        "backup",
        "notification"
    ]
    describe_parser.add_argument('type', choices=choices, help=message)
    message = "the ID of the resource to look up"
    describe_parser.add_argument('id', type=int, help=message)

    # Subparser for the Run method
    message = "run a backup job"
    run_parser = subparsers.add_parser('run', help=message)
    message = "the ID of the backup job to run"
    run_parser.add_argument('id', type=int, help=message)

    # Subparser for the Abort method
    message = "abort a task"
    abort_parser = subparsers.add_parser('abort', help=message)
    message = "the ID of the task to abort"
    abort_parser.add_argument('id', type=int, help=message)

    # Subparser for the Delete method
    message = "delete a backup"
    delete_parser = subparsers.add_parser('delete', help=message)
    choices = ["backup"]
    message = "the type of resource"
    delete_parser.add_argument('type', choices=choices, help=message)
    message = "the ID of the backup to delete"
    delete_parser.add_argument('id', type=int, help=message)
    message = "delete the local database"
    delete_parser.add_argument('--delete-db',
                               action='store_true', help=message)
    message = "confirm deletion non-interactively"
    delete_parser.add_argument('--confirm',
                               action='store_true', help=message)

    # Subparser for the Edit method
    message = "edit a resource on the server"
    edit_parser = subparsers.add_parser('edit', help=message)
    message = "the type of resource"
    edit_parser.add_argument('type', help=message)
    message = "the ID of the resource to edit"
    edit_parser.add_argument('id', type=int, help=message)

    # Subparser for the Export method
    message = "export a resource from the server to YAMl or JSON format"
    export_parser = subparsers.add_parser('export', help=message)
    choices = ["backup"]
    message = "the type of resource"
    export_parser.add_argument('type', choices=choices, help=message)
    message = "the ID of the resource to export"
    export_parser.add_argument('id', type=int, help=message)
    choices = [
        "YAML",
        "JSON",
        "yaml",
        "json"
    ]
    message = "output YAML or JSON, defaults to YAML"
    export_parser.add_argument('--output', help=message,
                               choices=choices, metavar='')
    message = "Path to output the file at"
    export_parser.add_argument('--output-path', metavar='', help=message)

    # Subparser for the Import method
    message = "import a resource to the server from a YAMl or JSON file"
    import_parser = subparsers.add_parser('import', help=message)
    message = "the type of resource"
    import_parser.add_argument('type', choices=["backup"], help=message)
    message = "file containing a job configuration in YAML or JSON format"
    import_parser.add_argument('import-file', nargs='?', help=message)
    message = "provide a backup id to update an existing backup"
    import_parser.add_argument('--id', metavar='', help=message)
    # Add mutual exclusion for the Import method
    group = import_parser.add_mutually_exclusive_group()
    message = "import the metadata when creating a backup"
    group.add_argument('--import-metadata', help=message, action='store_true')
    message = "strip the metadata before updating a backup"
    group.add_argument('--strip-metadata', help=message, action='store_true')

    # Subparser for the Logs method
    message = "display the logs for a given job"
    logs_parser = subparsers.add_parser('logs', help=message)
    choices = [
        "backup",
        "stored",
        "profiling",
        "information",
        "warning",
        "error"
    ]
    message = "backup, stored, profiling, information, warning, or error"
    logs_parser.add_argument('type', metavar='type',
                             choices=choices, help=message)
    message = "backup id"
    logs_parser.add_argument('--id', type=int, metavar='', help=message)
    message = "view backend logs for the backup job"
    logs_parser.add_argument('--remote', action='store_true', help=message)
    message = "periodically pool for new logs until interrupted"
    logs_parser.add_argument('--follow', action='store_true', help=message)
    message = "log lines to display"
    logs_parser.add_argument('--lines', action='store', default=5,
                             type=int, metavar='', help=message)
    message = "show all message and exception lines"
    logs_parser.add_argument('--all', action='store_true', help=message)

    # Subparser for the Login method
    message = "log into a Duplicati server"
    login_parser = subparsers.add_parser('login', help=message)
    login_parser.add_argument('url')
    message = "password, will prompt if not provided"
    login_parser.add_argument('--password', metavar='', help=message)
    message = "allow insecure HTTPS connections to the server"
    login_parser.add_argument('--insecure', action='store_true', help=message)
    message = "specify the path to certificate to be used for validation"
    login_parser.add_argument('--certfile', metavar='', help=message)
    message = "specify a non-standard configuration file"
    login_parser.add_argument('--config-file', help=message,
                              metavar='', action='store')

    # Subparser for the Logout method
    message = "end the current server session"
    subparsers.add_parser('logout', help=message)

    # Subparser for the Status method
    message = "return information about the current session"
    subparsers.add_parser('status', help=message)

    # Subparser for the Config method
    message = "prints the config to stdout"
    config_parser = subparsers.add_parser('config', help=message)
    message = "create a new configuration"
    config_parser.add_argument('--overwrite', action='store_true',
                               help=message)

    # Subparser for the Daemon mode
    message = "run Duplicati Client as a service"
    subparsers.add_parser('daemon', help=message)

    # Subparser for toggling verbose mode
    message = "toggle verbose mode"
    subparsers.add_parser('verbose', help=message)

    # Subparser for setting a parameter file
    message = "import parameters from a YAML file"
    params_parser = subparsers.add_parser('params', help=message)
    message = "path to file containing parameters in YAML format"
    params_parser.add_argument('param-file', nargs='?', help=message)
    message = "disable the parameters file"
    params_parser.add_argument('--disable', help=message, action='store_true')
    params_parser.add_argument('--show', help=message, action='store_true')

    # Construct parsers and initialize the main method
    args = parser.parse_args()
    main(**vars(args))

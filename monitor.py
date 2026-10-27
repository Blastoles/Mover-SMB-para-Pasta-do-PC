import os
import time
import shutil
import socket
import logging
import threading
import concurrent.futures
from datetime import datetime

def _is_unc_path(path):
    return path.startswith(r"\\") or path.startswith("//")

def _get_unc_host(path):
    cleaned = path.replace("/", "\\").lstrip("\\")
    parts = cleaned.split("\\")
    return parts[0] if parts else None

def _is_host_online(host, timeout=0.6):
    """
    Rapidly tests SMB ports 445 and 139 via TCP socket.
    Avoids 40-second kernel freezes on offline printers.
    """
    for port in (445, 139):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                if s.connect_ex((host, port)) == 0:
                    return True
        except Exception:
            pass
    return False

class ScanMonitor:
    def __init__(self, config_loader_callback, log_callback=None):
        """
        Initialize the Scan Monitor.
        :param config_loader_callback: A function that returns a list of rules (dicts).
        :param log_callback: A function that receives log messages (str).
        """
        self.config_loader_callback = config_loader_callback
        self.log_callback = log_callback
        self.stop_event = threading.Event()
        self.thread = None
        self.running = False
        self.active_rule_tasks = set()
        self.active_tasks_lock = threading.Lock()
        self.verified_sources = set()
        self.logged_offline = set()

    def log(self, message, level="INFO"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_message = f"[{timestamp}] [{level}] {message}"
        if self.log_callback:
            self.log_callback(formatted_message)
        else:
            print(formatted_message)

    def start(self):
        """Starts the monitoring thread loop."""
        if self.running:
            self.log("O monitor já está em execução.", "WARN")
            return
        
        self.stop_event.clear()
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, name="ScanMonitorLoop", daemon=True)
        self.thread.start()
        self.log("Serviço de monitoramento iniciado.", "INFO")

    def stop(self):
        """Stops the monitoring thread loop."""
        if not self.running:
            self.log("O monitor não está em execução.", "WARN")
            return
        
        self.log("Parando serviço de monitoramento...", "INFO")
        self.running = False
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self.verified_sources.clear()
        self.logged_offline.clear()
        with self.active_tasks_lock:
            self.active_rule_tasks.clear()
        self.log("Serviço de monitoramento parado.", "INFO")

    def is_running(self):
        return self.running

    def _is_file_ready(self, filepath):
        """
        Verifies if a file is ready to be moved (not locked and size is stable).
        """
        if not os.path.exists(filepath):
            return False
        
        try:
            # Check 1: Size stability
            s1 = os.path.getsize(filepath)
            time.sleep(1.0)
            s2 = os.path.getsize(filepath)
            if s1 != s2:
                return False
            
            # Check 2: Try opening the file to ensure no process has an exclusive write lock.
            with open(filepath, 'rb') as f:
                pass
            return True
        except (OSError, PermissionError):
            return False

    def _get_unique_destination_path(self, dest_dir, filename):
        """
        Generates a unique filename in the destination directory to avoid overwriting existing files.
        """
        base, ext = os.path.splitext(filename)
        counter = 1
        dest_path = os.path.join(dest_dir, filename)
        while os.path.exists(dest_path):
            dest_path = os.path.join(dest_dir, f"{base}_{counter}{ext}")
            counter += 1
        return dest_path

    def _process_rule(self, rule):
        """
        Processes a single rule recursively: scans source folder and subfolders,
        recreates subfolder structure at destination, and moves files.
        """
        rule_name = rule.get("name", "Regra Sem Nome")
        source = (rule.get("source") or "").strip()
        destination = (rule.get("destination") or "").strip()
        
        if not source or not destination:
            self.log(f"[{rule_name}] Caminho de origem ou destino não configurado.", "ERROR")
            return

        # Fix Python 3.15 bug: os.walk fails to add trailing slash to UNC roots (\\host\share)
        if _is_unc_path(source) and not source.endswith(os.sep) and not source.endswith('/'):
            source = source + os.sep

        # Fast pre-check for network UNC paths to avoid 40-second Windows kernel freeze
        if _is_unc_path(source):
            host = _get_unc_host(source)
            if host and not _is_host_online(host, timeout=0.6):
                self.verified_sources.discard(source)
                if source not in self.logged_offline:
                    self.logged_offline.add(source)
                    self.log(f"[{rule_name}] Impressora offline ({host} não responde na rede). Aguardando conexão...", "WARN")
                return
            elif source in self.logged_offline:
                self.logged_offline.discard(source)
                self.log(f"[{rule_name}] Impressora voltou a ficar online: '{source}'", "INFO")

        # Check if source directory exists
        try:
            if not os.path.exists(source):
                self.verified_sources.discard(source)
                self.log(f"[{rule_name}] Origem inacessível ou inexistente: '{source}'", "WARN")
                return
        except Exception as e:
            self.verified_sources.discard(source)
            self.log(f"[{rule_name}] Erro ao checar origem '{source}': {e}", "ERROR")
            return

        norm_source = os.path.normpath(os.path.abspath(source)).lower()
        norm_dest = os.path.normpath(os.path.abspath(destination)).lower()
        
        if norm_source == norm_dest:
            self.log(f"[{rule_name}] Origem e destino são idênticos: '{source}'. Ignorado para evitar loops.", "ERROR")
            return

        # Verify or create destination directory
        if not os.path.exists(destination):
            try:
                os.makedirs(destination, exist_ok=True)
                self.log(f"[{rule_name}] Pasta de destino criada: '{destination}'", "INFO")
            except Exception as e:
                self.log(f"[{rule_name}] Falha ao criar pasta de destino '{destination}': {e}", "ERROR")
                return

        # Log confirmation when origin becomes active
        if source not in self.verified_sources:
            self.verified_sources.add(source)
            self.log(f"[{rule_name}] Origem acessível: '{source}'. Monitorando...", "INFO")

        # Loop prevention: define prefix to identify if walking inside destination
        norm_dest_prefix = norm_dest + os.sep

        def on_walk_error(err):
            target = getattr(err, 'filename', source)
            self.log(f"[{rule_name}] Falha de permissão/acesso ao varrer '{target}': {err}", "ERROR")

        # Walk the source directory recursively
        try:
            for root, dirs, files in os.walk(source, onerror=on_walk_error):
                if self.stop_event.is_set():
                    break

                norm_root = os.path.normpath(os.path.abspath(root)).lower()
                
                # Check if current directory is destination or inside destination
                if norm_root == norm_dest or norm_root.startswith(norm_dest_prefix):
                    continue

                # Filter out subdirectories that match or are inside the destination folder to prevent recursion loop
                dirs[:] = [
                    d for d in dirs 
                    if os.path.normpath(os.path.abspath(os.path.join(root, d))).lower() != norm_dest and 
                    not os.path.normpath(os.path.abspath(os.path.join(root, d))).lower().startswith(norm_dest_prefix)
                ]

                # Determine destination folder mapping for this subfolder
                rel_path = os.path.relpath(root, source)
                if rel_path == ".":
                    target_dest_dir = destination
                else:
                    target_dest_dir = os.path.join(destination, rel_path)

                for file in files:
                    if self.stop_event.is_set():
                        break

                    # Skip temporary files
                    if file.lower() in ['thumbs.db', 'desktop.ini']:
                        continue

                    src_path = os.path.join(root, file)

                    # Ensure it is not a directory (handles Linux/Samba 0x0 attributes where isfile returns False)
                    if os.path.isdir(src_path):
                        continue

                    self.log(f"[{rule_name}] Detectado novo arquivo: '{os.path.join(rel_path, file) if rel_path != '.' else file}'. Aguardando estabilização...", "INFO")

                    # Wait until the file is ready to be moved
                    if self._is_file_ready(src_path):
                        # Ensure target destination subdirectory exists
                        if not os.path.exists(target_dest_dir):
                            try:
                                os.makedirs(target_dest_dir, exist_ok=True)
                                self.log(f"[{rule_name}] Subpasta de destino criada: '{target_dest_dir}'", "INFO")
                            except Exception as e:
                                self.log(f"[{rule_name}] Falha ao criar subpasta de destino '{target_dest_dir}': {e}", "ERROR")
                                continue

                        dest_path = self._get_unique_destination_path(target_dest_dir, file)
                        dest_filename = os.path.basename(dest_path)

                        try:
                            # Move the file safely
                            shutil.move(src_path, dest_path)

                            rel_dest_log = os.path.join(rel_path, dest_filename) if rel_path != '.' else dest_filename
                            if dest_filename != file:
                                self.log(f"[{rule_name}] Movido com sucesso: '{file}' -> '{rel_dest_log}' (conflito de nome resolvido)", "INFO")
                            else:
                                self.log(f"[{rule_name}] Movido com sucesso: '{file}' -> '{target_dest_dir}'", "INFO")
                        except Exception as e:
                            self.log(f"[{rule_name}] Erro ao mover arquivo '{file}': {e} (feche o arquivo se estiver aberto no navegador ou editor)", "ERROR")
                    else:
                        self.log(f"[{rule_name}] Arquivo '{file}' ainda em escrita ou bloqueado. Ignorado nesta rodada.", "WARN")

        except Exception as e:
            self.log(f"[{rule_name}] Falha ao percorrer arquivos na origem: {e}", "ERROR")
            return

        # Run auto-cleanup for this rule if configured
        self._run_cleanup(rule)

    def _run_cleanup(self, rule):
        """
        Deletes files in the destination directory (recursively) that are older than rule['cleanup_days'] days,
        and removes empty destination subfolders.
        """
        cleanup_days = rule.get("cleanup_days", 0)
        if not cleanup_days or int(cleanup_days) <= 0:
            return
            
        destination = rule.get("destination")
        rule_name = rule.get("name", "Regra Sem Nome")
        
        if not destination or not os.path.exists(destination):
            return
            
        try:
            now = time.time()
            threshold_seconds = int(cleanup_days) * 86400
            
            # Walk from bottom up to safely delete empty child directories after files are removed
            for root, dirs, files in os.walk(destination, topdown=False):
                if self.stop_event.is_set():
                    break
                    
                for file in files:
                    if self.stop_event.is_set():
                        break
                        
                    if file.lower() in ['thumbs.db', 'desktop.ini']:
                        continue
                        
                    filepath = os.path.join(root, file)
                    try:
                        mtime = os.path.getmtime(filepath)
                        age_seconds = now - mtime
                        
                        if age_seconds > threshold_seconds:
                            with open(filepath, 'r+b') as f:
                                pass
                            os.remove(filepath)
                            
                            rel_file_path = os.path.join(os.path.relpath(root, destination), file) if root != destination else file
                            self.log(f"[{rule_name}] Autolimpeza: Arquivo '{rel_file_path}' removido (mais antigo que {cleanup_days} dias).", "INFO")
                    except (OSError, PermissionError) as e:
                        self.log(f"[{rule_name}] Autolimpeza: Não foi possível excluir '{file}': {e}", "WARN")
                
                # Check and remove empty subfolders (do not remove root destination)
                if root != destination:
                    try:
                        if not os.listdir(root):
                            os.rmdir(root)
                            self.log(f"[{rule_name}] Autolimpeza: Pasta vazia '{os.path.relpath(root, destination)}' removida.", "INFO")
                    except Exception:
                        pass

        except Exception as e:
            self.log(f"[{rule_name}] Erro na autolimpeza: {e}", "ERROR")

    def _safe_process_rule(self, rule, rule_id):
        try:
            if not self.stop_event.is_set():
                self._process_rule(rule)
        except Exception as e:
            self.log(f"Erro inesperado processando regra '{rule.get('name')}': {e}", "ERROR")
        finally:
            with self.active_tasks_lock:
                self.active_rule_tasks.discard(rule_id)

    def _run_loop(self):
        """
        Background monitoring loop executing rules concurrently via ThreadPoolExecutor.
        """
        last_run = {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            while not self.stop_event.is_set():
                try:
                    rules = self.config_loader_callback()
                except Exception as e:
                    self.log(f"Erro ao carregar configurações: {e}", "ERROR")
                    time.sleep(5)
                    continue

                current_time = time.time()

                for rule in rules:
                    if self.stop_event.is_set():
                        break

                    if not rule.get("enabled", False):
                        continue

                    rule_id = rule.get("id")
                    interval = max(1, int(rule.get("interval", 5)))

                    if rule_id not in last_run or (current_time - last_run[rule_id]) >= interval:
                        last_run[rule_id] = current_time
                        with self.active_tasks_lock:
                            if rule_id not in self.active_rule_tasks:
                                self.active_rule_tasks.add(rule_id)
                                executor.submit(self._safe_process_rule, rule, rule_id)

                # Sleep in short increments to remain responsive to stop_event
                for _ in range(10):
                    if self.stop_event.is_set():
                        break
                    time.sleep(0.1)

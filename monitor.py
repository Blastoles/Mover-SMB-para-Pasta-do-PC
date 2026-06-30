import os
import time
import shutil
import logging
import threading
from datetime import datetime

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
        self.active_rules_threads = {}

    def log(self, message, level="INFO"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_message = f"[{timestamp}] [{level}] {message}"
        if self.log_callback:
            self.log_callback(formatted_message)
        else:
            print(formatted_message)

    def start(self):
        """Starts the monitoring thread loop."""
        if self.thread and self.thread.is_alive():
            self.log("O monitor já está em execução.", "WARN")
            return
        
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run_loop, name="ScanMonitorLoop", daemon=True)
        self.thread.start()
        self.log("Serviço de monitoramento iniciado.", "INFO")

    def stop(self):
        """Stops the monitoring thread loop."""
        if not self.thread or not self.thread.is_alive():
            self.log("O monitor não está em execução.", "WARN")
            return
        
        self.log("Parando serviço de monitoramento...", "INFO")
        self.stop_event.set()
        self.thread.join(timeout=3)
        self.log("Serviço de monitoramento parado.", "INFO")

    def is_running(self):
        return self.thread is not None and self.thread.is_alive()

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
            
            # Check 2: Try opening the file to ensure no other process (like scanner) is writing to it.
            # On Windows, checking with 'rb' (read-only) will fail if another process has an exclusive lock.
            with open(filepath, 'rb') as f:
                pass
            return True
        except (OSError, PermissionError):
            # File is locked or inaccessible
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
        Processes a single rule: scans source folder and moves files.
        """
        rule_name = rule.get("name", "Regra Sem Nome")
        source = rule.get("source")
        destination = rule.get("destination")
        
        if not source or not destination:
            self.log(f"[{rule_name}] Caminho de origem ou destino não configurado.", "ERROR")
            return

        # Check if source directory exists
        if not os.path.exists(source):
            self.log(f"[{rule_name}] Origem inacessível ou inexistente: '{source}'", "WARN")
            return

        # Verify or create destination directory
        if not os.path.exists(destination):
            try:
                os.makedirs(destination, exist_ok=True)
                self.log(f"[{rule_name}] Pasta de destino criada: '{destination}'", "INFO")
            except Exception as e:
                self.log(f"[{rule_name}] Falha ao criar pasta de destino '{destination}': {e}", "ERROR")
                return

        # List files in source directory
        try:
            items = os.listdir(source)
        except Exception as e:
            self.log(f"[{rule_name}] Falha ao listar arquivos na origem: {e}", "ERROR")
            return

        for item in items:
            # Check if stop requested mid-process
            if self.stop_event.is_set():
                break

            src_path = os.path.join(source, item)
            
            # Skip directories, process only files
            if not os.path.isfile(src_path):
                continue
                
            # Skip temporary scan files if any exist (e.g. system files like thumbs.db or temporary printer locks)
            if item.lower() in ['thumbs.db', 'desktop.ini']:
                continue

            self.log(f"[{rule_name}] Detectado novo arquivo: '{item}'. Aguardando estabilização...", "INFO")
            
            # Wait until the file is ready to be moved
            if self._is_file_ready(src_path):
                dest_path = self._get_unique_destination_path(destination, item)
                dest_filename = os.path.basename(dest_path)
                
                try:
                    # Move the file safely
                    # shutil.move handles cross-volume movement by copying and deleting,
                    # which is perfect if source is network share and dest is local C: drive
                    shutil.move(src_path, dest_path)
                    
                    if dest_filename != item:
                        self.log(f"[{rule_name}] Movido com sucesso: '{item}' -> '{dest_filename}' (conflito de nome resolvido)", "INFO")
                    else:
                        self.log(f"[{rule_name}] Movido com sucesso: '{item}' -> '{destination}'", "INFO")
                except Exception as e:
                    self.log(f"[{rule_name}] Erro ao mover arquivo '{item}': {e}", "ERROR")
            else:
                self.log(f"[{rule_name}] Arquivo '{item}' ainda em escrita ou bloqueado. Ignorado nesta rodada.", "WARN")
        
        # Run auto-cleanup for this rule if configured
        self._run_cleanup(rule)

    def _run_cleanup(self, rule):
        """
        Deletes files in the destination directory that are older than rule['cleanup_days'] days.
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
            
            items = os.listdir(destination)
            for item in items:
                if self.stop_event.is_set():
                    break
                    
                filepath = os.path.join(destination, item)
                if not os.path.isfile(filepath):
                    continue
                    
                if item.lower() in ['thumbs.db', 'desktop.ini']:
                    continue
                    
                mtime = os.path.getmtime(filepath)
                age_seconds = now - mtime
                
                if age_seconds > threshold_seconds:
                    try:
                        # Ensure it's not locked by trying to open it read-write exclusively
                        with open(filepath, 'r+b') as f:
                            pass
                        
                        os.remove(filepath)
                        self.log(f"[{rule_name}] Autolimpeza: Arquivo '{item}' removido (mais antigo que {cleanup_days} dias).", "INFO")
                    except (OSError, PermissionError) as e:
                        self.log(f"[{rule_name}] Autolimpeza: Não foi possível excluir '{item}' pois está em uso: {e}", "WARN")
        except Exception as e:
            self.log(f"[{rule_name}] Erro na autolimpeza: {e}", "ERROR")

    def _run_loop(self):
        """
        Background monitoring loop.
        """
        # Tracks the last time each rule was run to respect its individual check interval
        last_run = {}

        while not self.stop_event.is_set():
            try:
                # Load configurations dynamically
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
                interval = max(1, int(rule.get("interval", 5))) # minimum 1 second

                # If it's time to run this rule
                if rule_id not in last_run or (current_time - last_run[rule_id]) >= interval:
                    # Process the rule
                    try:
                        self._process_rule(rule)
                    except Exception as e:
                        self.log(f"Erro inesperado processando regra '{rule.get('name')}': {e}", "ERROR")
                    
                    last_run[rule_id] = time.time()

            # Sleep in short increments to remain responsive to stop_event
            for _ in range(10):
                if self.stop_event.is_set():
                    break
                time.sleep(0.1)

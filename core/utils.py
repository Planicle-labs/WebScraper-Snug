import os
import json
import psutil
from typing import Dict, Any, List, Optional


def get_system_metrics() -> Dict[str, Any]:
    """
    Returns current system CPU, RAM, and network usage.
    
    Returns:
        Dict with cpu_percent, ram_percent, bytes_sent, bytes_recv
    """
    net = psutil.net_io_counters()
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "ram_percent": psutil.virtual_memory().percent,
        "bytes_sent": net.bytes_sent,
        "bytes_recv": net.bytes_recv,
    }


def get_process_metrics() -> Dict[str, Any]:
    """
    Returns metrics for the current process (scraper).
    
    Returns:
        Dict with memory usage info
    """
    process = psutil.Process()
    mem_info = process.memory_info()
    return {
        "rss_mb": mem_info.rss / (1024 * 1024),
        "vms_mb": mem_info.vms / (1024 * 1024),
        "cpu_percent": process.cpu_percent(interval=0.1),
        "num_threads": process.num_threads(),
    }


def read_json_file(file_path: str) -> Optional[Any]:
    """
    Read and parse a JSON file.
    
    Args:
        file_path: Path to the JSON file
        
    Returns:
        Parsed JSON data or None if file doesn't exist
    """
    if not os.path.exists(file_path):
        return None
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        return None


def get_output_file_path(filename: str) -> str:
    """
    Get the full path to an output file in the outputs directory.
    
    Args:
        filename: Name of the file (e.g., 'product_pages.json')
        
    Returns:
        Full path to the output file
    """
    _THIS_DIR = os.path.dirname(os.path.abspath(__file__))
    _ROOT = os.path.dirname(_THIS_DIR)
    return os.path.join(_ROOT, "outputs", filename)
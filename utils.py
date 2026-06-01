"""
Utility functions for Document Intelligence System
"""
import json
import logging
from typing import Any, Dict, List
from datetime import datetime
from pathlib import Path


logger = logging.getLogger(__name__)


class JSONEncoder(json.JSONEncoder):
    """Custom JSON encoder for special types"""
    
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Path):
            return str(obj)
        if hasattr(obj, '__dict__'):
            return obj.__dict__
        return super().default(obj)


def save_json(data: Dict[str, Any], filepath: str) -> bool:
    """Save data to JSON file"""
    try:
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2, cls=JSONEncoder)
        logger.info(f"Saved data to {filepath}")
        return True
    except Exception as e:
        logger.error(f"Failed to save JSON: {e}")
        return False


def load_json(filepath: str) -> Dict[str, Any]:
    """Load data from JSON file"""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
        logger.info(f"Loaded data from {filepath}")
        return data
    except Exception as e:
        logger.error(f"Failed to load JSON: {e}")
        return {}


def format_confidence(confidence: float) -> str:
    """Format confidence as percentage"""
    return f"{confidence * 100:.1f}%"


def format_file_size(size_bytes: int) -> str:
    """Format bytes as human readable size"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def merge_dicts(*dicts: Dict) -> Dict:
    """Merge multiple dictionaries"""
    result = {}
    for d in dicts:
        if d:
            result.update(d)
    return result


def filter_empty_values(data: Dict) -> Dict:
    """Remove empty values from dictionary"""
    return {k: v for k, v in data.items() if v is not None and v != ''}


def batch_items(items: List, batch_size: int) -> List[List]:
    """Batch items into smaller lists"""
    batches = []
    for i in range(0, len(items), batch_size):
        batches.append(items[i:i + batch_size])
    return batches


def setup_logging(level: str = "INFO") -> None:
    """Setup logging configuration"""
    logging.basicConfig(
        level=getattr(logging, level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('app.log'),
            logging.StreamHandler()
        ]
    )

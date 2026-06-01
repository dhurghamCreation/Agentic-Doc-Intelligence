"""
Table Parser for extracting structured data from document tables.
"""
import pandas as pd
import numpy as np
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class TableCell:
    """Represents a single table cell"""
    content: str
    row: int
    col: int
    confidence: float
    bbox: Optional[Dict] = None


@dataclass
class ParsedTable:
    """Parsed table structure"""
    headers: List[str]
    rows: List[List[str]]
    raw_data: Optional[pd.DataFrame] = None
    confidence: float = 0.0


class TableParser:
    """Parse and extract structured data from tables"""
    
    def __init__(self):
        self.min_cell_confidence = 0.5
    
    def parse_table_from_image(
        self, 
        image_path: str,
        ocr_results: List[Dict]
    ) -> Optional[ParsedTable]:
        """Parse table from image using OCR results"""
        try:
            # Group OCR results by table structure
            cells = self._group_cells(ocr_results)
            
            if not cells:
                return None
            
            # Build table structure
            headers = self._extract_headers(cells)
            rows = self._build_rows(cells, len(headers))
            
            # Create DataFrame
            df = pd.DataFrame(rows, columns=headers)
            
            # Calculate confidence
            confidences = [cell.confidence for cell in cells]
            avg_confidence = np.mean(confidences) if confidences else 0.0
            
            return ParsedTable(
                headers=headers,
                rows=rows,
                raw_data=df,
                confidence=avg_confidence
            )
            
        except Exception as e:
            logger.error(f"Table parsing failed: {str(e)}")
            return None
    
    def parse_table_from_text(
        self,
        text: str,
        delimiter: str = '\t'
    ) -> Optional[ParsedTable]:
        """Parse table from delimited text"""
        try:
            lines = text.strip().split('\n')
            if not lines:
                return None
            
            # Parse header
            headers = lines[0].split(delimiter)
            
            # Parse rows
            rows = []
            for line in lines[1:]:
                if line.strip():
                    rows.append(line.split(delimiter))
            
            # Create DataFrame
            df = pd.DataFrame(rows, columns=headers)
            
            return ParsedTable(
                headers=headers,
                rows=rows,
                raw_data=df,
                confidence=1.0
            )
            
        except Exception as e:
            logger.error(f"Text table parsing failed: {str(e)}")
            return None
    
    def _group_cells(self, ocr_results: List[Dict]) -> List[TableCell]:
        """Group OCR results into table cells"""
        cells = []
        for result in ocr_results:
            if result.get('confidence', 0) >= self.min_cell_confidence:
                cell = TableCell(
                    content=result.get('text', ''),
                    row=result.get('row', 0),
                    col=result.get('col', 0),
                    confidence=result.get('confidence', 0),
                    bbox=result.get('bbox')
                )
                cells.append(cell)
        return cells
    
    def _extract_headers(self, cells: List[TableCell]) -> List[str]:
        """Extract table headers from top row"""
        if not cells:
            return []
        
        # Get first row cells
        header_cells = [c for c in cells if c.row == 0]
        header_cells.sort(key=lambda x: x.col)
        
        return [cell.content for cell in header_cells]
    
    def _build_rows(self, cells: List[TableCell], num_cols: int) -> List[List[str]]:
        """Build table rows from cells"""
        if not cells:
            return []
        
        # Group by row
        rows_dict = {}
        for cell in cells:
            if cell.row > 0:  # Skip header row
                if cell.row not in rows_dict:
                    rows_dict[cell.row] = {}
                rows_dict[cell.row][cell.col] = cell.content
        
        # Convert to list of lists
        rows = []
        for row_num in sorted(rows_dict.keys()):
            row_data = rows_dict[row_num]
            row = [row_data.get(col, '') for col in range(num_cols)]
            rows.append(row)
        
        return rows
    
    def validate_table_structure(self, parsed_table: ParsedTable) -> bool:
        """Validate parsed table structure"""
        if not parsed_table or not parsed_table.rows:
            return False
        
        # Check consistency
        expected_cols = len(parsed_table.headers)
        for row in parsed_table.rows:
            if len(row) != expected_cols:
                return False
        
        return parsed_table.confidence >= self.min_cell_confidence
    
    def export_to_csv(self, parsed_table: ParsedTable, output_path: str) -> bool:
        """Export parsed table to CSV"""
        try:
            if parsed_table.raw_data is not None:
                parsed_table.raw_data.to_csv(output_path, index=False)
                return True
            return False
        except Exception as e:
            logger.error(f"CSV export failed: {str(e)}")
            return False
    
    def export_to_json(self, parsed_table: ParsedTable, output_path: str) -> bool:
        """Export parsed table to JSON"""
        try:
            if parsed_table.raw_data is not None:
                parsed_table.raw_data.to_json(output_path, orient='records')
                return True
            return False
        except Exception as e:
            logger.error(f"JSON export failed: {str(e)}")
            return False

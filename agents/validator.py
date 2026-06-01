"""
Validator Agent - Validates extracted data and ensures data quality.
Performs consistency checks, format validation, and anomaly detection.
"""
import re
import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)


class ValidationStatus(str, Enum):
    """Validation status"""
    VALID = "valid"
    WARNING = "warning"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass
class ValidationIssue:
    """A validation issue found"""
    field: str
    issue_type: str
    message: str
    severity: ValidationStatus
    suggestion: Optional[str] = None


@dataclass
class ValidationResult:
    """Result from data validation"""
    status: ValidationStatus
    is_valid: bool
    issues: List[ValidationIssue]
    warnings: List[ValidationIssue]
    data_quality_score: float
    confidence: float


class DataValidator:
    """Validate extracted data quality"""
    
    # Validation rules for different field types
    VALIDATION_RULES = {
        'email': {
            'pattern': r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}$',
            'required': False,
            'max_length': 100,
            'description': 'Must be valid email format'
        },
        'phone': {
            'pattern': r'^[\+]?[(]?[0-9]{3}[)]?[-\s\.]?[0-9]{3}[-\s\.]?[0-9]{4,6}$',
            'required': False,
            'description': 'Must be valid phone format'
        },
        'date': {
            'pattern': r'^\d{4}-\d{2}-\d{2}$|^\d{1,2}/\d{1,2}/\d{2,4}$',
            'required': False,
            'description': 'Must be valid date format'
        },
        'amount': {
            'pattern': r'^\d+(\.\d{2})?$',
            'required': False,
            'min_value': 0,
            'description': 'Must be positive number'
        },
        'invoice_number': {
            'required': True,
            'min_length': 1,
            'max_length': 50,
            'description': 'Invoice number is required'
        },
    }
    
    def __init__(self, strict_mode: bool = False):
        """Initialize validator"""
        self.strict_mode = strict_mode
        self.compiled_patterns = {}
        self._compile_patterns()
    
    def _compile_patterns(self):
        """Compile regex patterns"""
        for field, rules in self.VALIDATION_RULES.items():
            if 'pattern' in rules:
                try:
                    self.compiled_patterns[field] = re.compile(rules['pattern'])
                except Exception as e:
                    logger.warning(f"Failed to compile pattern for {field}: {e}")
    
    def validate(
        self,
        data: Dict[str, Any],
        schema: Optional[Dict] = None
    ) -> ValidationResult:
        """
        Validate extracted data against schema.
        
        Args:
            data: Data to validate
            schema: Validation schema (uses defaults if not provided)
        
        Returns:
            ValidationResult with issues and quality score
        """
        try:
            issues = []
            warnings = []
            
            # Use provided schema or default
            validation_schema = schema or self._get_default_schema(data)
            
            # Validate each field
            for field_name, field_value in data.items():
                field_issues = self._validate_field(
                    field_name,
                    field_value,
                    validation_schema.get(field_name, {})
                )
                
                if field_issues:
                    for issue in field_issues:
                        if issue.severity == ValidationStatus.ERROR:
                            issues.append(issue)
                        else:
                            warnings.append(issue)
            
            # Calculate quality score
            quality_score = self._calculate_quality_score(data, issues)
            
            # Determine overall status
            if issues:
                status = ValidationStatus.ERROR
                is_valid = False
            elif warnings:
                status = ValidationStatus.WARNING
                is_valid = True
            else:
                status = ValidationStatus.VALID
                is_valid = True
            
            return ValidationResult(
                status=status,
                is_valid=is_valid,
                issues=issues,
                warnings=warnings,
                data_quality_score=quality_score,
                confidence=1.0 - (len(issues) * 0.1)
            )
            
        except Exception as e:
            logger.error(f"Validation failed: {str(e)}")
            return ValidationResult(
                status=ValidationStatus.UNKNOWN,
                is_valid=False,
                issues=[],
                warnings=[],
                data_quality_score=0.0,
                confidence=0.0
            )
    
    def _validate_field(
        self,
        field_name: str,
        field_value: Any,
        rules: Dict
    ) -> List[ValidationIssue]:
        """Validate a single field"""
        issues = []
        
        if field_value is None or field_value == '':
            if rules.get('required', False):
                issues.append(ValidationIssue(
                    field=field_name,
                    issue_type='missing_required',
                    message=f"{field_name} is required",
                    severity=ValidationStatus.ERROR,
                    suggestion=f"Provide a value for {field_name}"
                ))
            return issues
        
        value_str = str(field_value)
        
        # Check length
        if 'min_length' in rules:
            if len(value_str) < rules['min_length']:
                issues.append(ValidationIssue(
                    field=field_name,
                    issue_type='too_short',
                    message=f"{field_name} is too short",
                    severity=ValidationStatus.WARNING
                ))
        
        if 'max_length' in rules:
            if len(value_str) > rules['max_length']:
                issues.append(ValidationIssue(
                    field=field_name,
                    issue_type='too_long',
                    message=f"{field_name} exceeds max length",
                    severity=ValidationStatus.WARNING
                ))
        
        # Check format/pattern
        if 'pattern' in rules:
            pattern = self.compiled_patterns.get(field_name)
            if pattern and not pattern.match(value_str):
                issues.append(ValidationIssue(
                    field=field_name,
                    issue_type='invalid_format',
                    message=f"{field_name} format is invalid",
                    severity=ValidationStatus.ERROR if self.strict_mode else ValidationStatus.WARNING,
                    suggestion=rules.get('description')
                ))
        
        # Check numeric constraints
        if 'min_value' in rules:
            try:
                num_value = float(value_str.replace('$', '').replace(',', ''))
                if num_value < rules['min_value']:
                    issues.append(ValidationIssue(
                        field=field_name,
                        issue_type='value_too_low',
                        message=f"{field_name} is below minimum value",
                        severity=ValidationStatus.WARNING
                    ))
            except ValueError:
                pass
        
        if 'max_value' in rules:
            try:
                num_value = float(value_str.replace('$', '').replace(',', ''))
                if num_value > rules['max_value']:
                    issues.append(ValidationIssue(
                        field=field_name,
                        issue_type='value_too_high',
                        message=f"{field_name} exceeds maximum value",
                        severity=ValidationStatus.WARNING
                    ))
            except ValueError:
                pass
        
        return issues
    
    def _get_default_schema(self, data: Dict) -> Dict:
        """Generate default schema from data"""
        schema = {}
        for field_name in data.keys():
            # Match against known rules
            if field_name in self.VALIDATION_RULES:
                schema[field_name] = self.VALIDATION_RULES[field_name]
            else:
                # Generic text validation
                schema[field_name] = {'required': False}
        return schema
    
    def _calculate_quality_score(
        self,
        data: Dict[str, Any],
        issues: List[ValidationIssue]
    ) -> float:
        """Calculate overall data quality score"""
        if not data:
            return 0.0
        
        # Base score
        score = 1.0
        
        # Deduct for issues
        score -= len(issues) * 0.1
        
        # Deduct for missing optional fields
        total_fields = len(data)
        non_empty_fields = sum(1 for v in data.values() if v is not None and v != '')
        
        if total_fields > 0:
            completeness = non_empty_fields / total_fields
            score *= completeness
        
        return max(0.0, min(1.0, score))
    
    def validate_consistency(self, records: List[Dict]) -> Dict[str, Any]:
        """Check consistency across multiple records"""
        if not records:
            return {'valid': True, 'issues': []}
        
        consistency_issues = []
        
        # Check for duplicate data
        seen_invoices = set()
        for record in records:
            invoice_id = record.get('invoice_number', '')
            if invoice_id in seen_invoices:
                consistency_issues.append({
                    'type': 'duplicate_invoice',
                    'value': invoice_id,
                    'severity': 'warning'
                })
            seen_invoices.add(invoice_id)
        
        # Check for anomalies
        amounts = []
        for record in records:
            amount = record.get('total_amount')
            if amount:
                try:
                    amounts.append(float(str(amount).replace('$', '').replace(',', '')))
                except ValueError:
                    pass
        
        if amounts:
            avg_amount = sum(amounts) / len(amounts)
            for i, amount in enumerate(amounts):
                if amount > avg_amount * 2:
                    consistency_issues.append({
                        'type': 'anomaly_detected',
                        'record_index': i,
                        'value': amount,
                        'average': avg_amount,
                        'severity': 'warning'
                    })
        
        return {
            'valid': len(consistency_issues) == 0,
            'issues': consistency_issues
        }

"""
Example usage and testing of Document Intelligence System
"""
import asyncio
from app.pipeline import DocumentProcessingPipeline
import json


async def example_single_document():
    """Example: Process a single document"""
    print("=" * 60)
    print("Example 1: Process Single Document")
    print("=" * 60)
    
    pipeline = DocumentProcessingPipeline()
    
    # Sample invoice text
    invoice_text = """
    INVOICE
    Invoice Number: INV-2024-001
    Date: 01/15/2024
    Due Date: 02/15/2024
    
    From: Acme Corporation
    123 Business Ave
    New York, NY 10001
    
    Bill To: ABC Company
    456 Corporate Rd
    Los Angeles, CA 90001
    
    Items:
    - Widget A: $100.00
    - Service B: $250.00
    - Widget C: $150.00
    
    Subtotal: $500.00
    Tax: $50.00
    Total: $550.00
    
    Payment Terms: Net 30
    """
    
    # Process document
    result = await pipeline.process_document(
        document_id="invoice_001",
        text=invoice_text
    )
    
    # Print results
    print(f"\n✓ Classification: {result.classification.document_type.value}")
    print(f"  Confidence: {result.classification.confidence:.2%}")
    
    print(f"\n✓ Extraction ({len(result.extraction.extracted_fields)} fields):")
    for field in result.extraction.extracted_fields[:5]:
        print(f"  - {field.name}: {field.value} ({field.confidence:.2%})")
    
    print(f"\n✓ Validation: {result.validation.status.value}")
    print(f"  Data Quality: {result.validation.data_quality_score:.2%}")
    
    print(f"\n✓ Processing Time: {result.processing_time:.2f}s")
    
    return result


async def example_batch():
    """Example: Batch process multiple documents"""
    print("\n" + "=" * 60)
    print("Example 2: Batch Process Documents")
    print("=" * 60)
    
    pipeline = DocumentProcessingPipeline()
    
    documents = [
        {
            "id": "doc_1",
            "text": "Receipt for purchase on 01/20/2024. Total: $99.99. Thank you!"
        },
        {
            "id": "doc_2",
            "text": "This Agreement is entered into between Party A and Party B..."
        },
        {
            "id": "doc_3",
            "text": "Dear Sir/Madam, Please find the attached report. Best regards."
        }
    ]
    
    results = await pipeline.process_batch(documents)
    stats = pipeline.get_statistics(results)
    
    print(f"\n✓ Processed {stats['total_documents']} documents")
    print(f"  Success Rate: {stats['success_rate']:.1%}")
    print(f"  Average Time: {stats['average_processing_time']:.2f}s")
    
    print(f"\n✓ Document Types:")
    for doc_type, count in stats['document_types'].items():
        print(f"  - {doc_type}: {count}")
    
    print(f"\n✓ Data Quality: {stats['data_quality_avg']:.2%}")


async def example_extraction():
    """Example: Custom extraction"""
    print("\n" + "=" * 60)
    print("Example 3: Custom Field Extraction")
    print("=" * 60)
    
    pipeline = DocumentProcessingPipeline()
    
    # Custom extraction patterns
    custom_fields = {
        "order_date": r"order.*?date.*?(\d{1,2}/\d{1,2}/\d{2,4})",
        "customer_id": r"customer.*?(?:id|#)\s*:?\s*(\w+)",
        "product_sku": r"(?:sku|product).*?(?:id|#)\s*:?\s*(\w+)"
    }
    
    text = """
    Order Date: 01/15/2024
    Customer ID: CUST-12345
    Product SKU: PROD-98765
    
    Quantity: 5
    Unit Price: $25.00
    Total: $125.00
    """
    
    result = await pipeline.process_document(
        document_id="custom_001",
        text=text,
        custom_extraction_schema=custom_fields
    )
    
    print(f"\n✓ Extracted Custom Fields:")
    for field in result.extraction.extracted_fields:
        print(f"  - {field.name}: {field.value}")


async def example_validation():
    """Example: Data validation"""
    print("\n" + "=" * 60)
    print("Example 4: Data Validation")
    print("=" * 60)
    
    from agents.validator import DataValidator
    
    validator = DataValidator()
    
    # Data with potential issues
    data = {
        "email": "invalid-email",
        "invoice_number": "INV-2024-001",
        "amount": "1000.00",
        "phone": "+1-555-1234"
    }
    
    result = validator.validate(data)
    
    print(f"\n✓ Validation Status: {result.status.value}")
    print(f"  Is Valid: {result.is_valid}")
    print(f"  Quality Score: {result.data_quality_score:.2%}")
    
    if result.issues:
        print(f"\n✗ Issues Found:")
        for issue in result.issues:
            print(f"  - {issue.field}: {issue.message}")
            if issue.suggestion:
                print(f"    Suggestion: {issue.suggestion}")


async def main():
    """Run all examples"""
    print("\n" + "🚀" * 30)
    print("DOCUMENT INTELLIGENCE SYSTEM - EXAMPLES")
    print("🚀" * 30 + "\n")
    
    # Run examples
    await example_single_document()
    await example_batch()
    await example_extraction()
    await example_validation()
    
    print("\n" + "=" * 60)
    print("Examples Complete!")
    print("=" * 60)
    print("\nTo run the web application:")
    print("  python main.py")
    print("\nThen visit:")
    print("  http://localhost:8000/dashboard")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())

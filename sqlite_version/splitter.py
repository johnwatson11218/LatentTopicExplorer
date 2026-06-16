import os
from pathlib import Path
from pypdf import PdfReader, PdfWriter

def split_pdf_to_single_pages(
    source,                    # Can be: file path (str/Path) OR raw bytes (from DB)
    output_dir: str = "data/split_pages",
    prefix: str = "",          # e.g. "doc123_" or "invoice_"
    zero_pad: int = 4          # How many digits: 0001, 0002, ...
) -> list[str]:
    """
    Splits a PDF into one-page PDFs.
    Returns a list of created file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    reader = PdfReader(source)   # pypdf accepts bytes directly

    created_files = []

    for page_num in range(len(reader.pages)):
        writer = PdfWriter()
        writer.add_page(reader.pages[page_num])

        # Create nice filename: e.g. prefix0001.pdf  or  page_0005.pdf
        page_name = f"{prefix}{str(page_num + 1).zfill(zero_pad)}.pdf"
        output_path = os.path.join(output_dir, page_name)

        with open(output_path, "wb") as f:
            writer.write(f)

        created_files.append(output_path)
        print(f"Saved: {output_path}")

    print(f"✓ Split completed: {len(created_files)} single-page PDFs created in '{output_dir}'")
    return created_files

print( split_pdf_to_single_pages( './data/test.pdf'))
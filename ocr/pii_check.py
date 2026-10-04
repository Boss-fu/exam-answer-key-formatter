import sys
import os
import re
from pathlib import Path
try:
    from docx import Document
except ImportError:
    print("python-docx not installed.")
    sys.exit(1)

def extract_text_from_docx(docx_path):
    try:
        doc = Document(docx_path)
    except Exception as e:
        return []
    
    segments = []
    idx = 1
    
    # paragraphs
    for p in doc.paragraphs:
        if p.text.strip():
            segments.append((f"第 {idx} 段", p.text))
        idx += 1
        
    # tables
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    segments.append((f"第 {idx} 段 (表格)", cell.text))
        idx += 1
                
    # headers and footers
    for section in doc.sections:
        for p in section.header.paragraphs:
            if p.text.strip():
                segments.append((f"第 {idx} 段 (頁首)", p.text))
            idx += 1
        for table in section.header.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        segments.append((f"第 {idx} 段 (頁首表格)", cell.text))
            idx += 1
        for p in section.footer.paragraphs:
            if p.text.strip():
                segments.append((f"第 {idx} 段 (頁尾)", p.text))
            idx += 1
        for table in section.footer.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        segments.append((f"第 {idx} 段 (頁尾表格)", cell.text))
            idx += 1
                    
    return segments

def check_pii(text):
    suspects = []
    
    # (姓名|座號|學號|班級) followed by optional spaces, colons, underscores, full-width spaces
    # and then 2~4 Chinese characters or digits
    pattern = re.compile(r'(姓名|座號|學號|班級)[\s:：_\u3000]*([\u4e00-\u9fa50-9]{2,4})')
    for match in pattern.finditer(text):
        label = match.group(1)
        content = match.group(2)
        
        ok_patterns = [
            r'^(班級|座號|姓名|學號|年級|年|班|號)',
            r'^(命題|審題|教師|老師|下學期|上學期|得分|分數|科目|範圍)',
            r'^第.*次'
        ]
        
        if any(re.match(p, content) for p in ok_patterns):
            continue
            
        masked = ""
        if label == "姓名":
            masked = content[0] + "○" * (len(content) - 1)
        else:
            masked = re.sub(r'\d', '*', content)
            
        suspects.append(("基本個資", f"{label}：{masked}"))
        
    # check for 保佑/加油/必勝
    bless_pattern = re.compile(r'([\u4e00-\u9fa5]{2,3})(保佑|加油|必勝)')
    for match in bless_pattern.finditer(text):
        name = match.group(1)
        word = match.group(2)
        masked_name = name[0] + "○" * (len(name) - 1)
        suspects.append(("塗鴉外洩", f"{masked_name}{word}"))
        
    return suspects

def main():
    if len(sys.argv) < 2:
        print("Usage: python pii_check.py <docx_file_or_dir> ...")
        sys.exit(1)
        
    has_error = False
    
    for arg in sys.argv[1:]:
        p = Path(arg)
        files_to_check = []
        if p.is_file() and p.suffix.lower() == '.docx':
            files_to_check.append(p)
        elif p.is_dir():
            files_to_check.extend(p.rglob('*.docx'))
            
        for docx_file in files_to_check:
            if docx_file.name.startswith('~$'): continue
            
            segments = extract_text_from_docx(docx_file)
            file_suspects = []
            
            for seg_name, text in segments:
                suspects = check_pii(text)
                for rule_name, s in suspects:
                    file_suspects.append(f"[{seg_name}] [{rule_name}] {s}")
            
            if file_suspects:
                has_error = True
                print(f"✗ {docx_file.name}")
                for s in file_suspects:
                    print(f"  - {s}")
            else:
                print(f"OK {docx_file.name}")
                
    if has_error:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == '__main__':
    main()

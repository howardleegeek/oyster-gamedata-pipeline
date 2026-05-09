# SD — rc10 自更新链路鲁棒 (B3+B4)

修改 `bin/recorder_consumer_lite.py` 函数 `_stage_self_update(new_exe_url: str)` (大约 line 181–250).

## 当前代码 (要替换的关键片段)

第 ~205 行附近:
```python
        new_path = Path(tempfile.gettempdir()) / "OysterRecorder-update.exe"
```

第 ~221–229 行附近 (bat 内容):
```python
    bat_body = (
        "@echo off\r\n"
        "rem Wait for the recorder to fully exit before swapping.\r\n"
        "timeout /t 3 /nobreak > nul\r\n"
        f'move /Y "{new_path}" "{current_exe}"\r\n'
        f'start "" "{current_exe}"\r\n'
        f'del "{bat_path}"\r\n'
    )
```

## 改动

### B4: download 路径同盘
把 `new_path = Path(tempfile.gettempdir()) / "OysterRecorder-update.exe"` 改为:
```python
        target_dir = Path(sys.executable).parent / "_update_tmp"
        target_dir.mkdir(parents=True, exist_ok=True)
        new_path = target_dir / "OysterRecorder-update.exe"
```

### B3: bat 加 retry loop
把整个 bat_body 改为:
```python
    bat_body = (
        "@echo off\r\n"
        "rem rc10 B3: retry move up to 30s while .exe handle is released.\r\n"
        "set RETRY=0\r\n"
        ":retry\r\n"
        "timeout /t 1 /nobreak > nul\r\n"
        f'move /Y "{new_path}" "{current_exe}" 2>nul\r\n'
        "if errorlevel 1 (\r\n"
        "  set /a RETRY+=1\r\n"
        "  if %RETRY% LSS 30 goto retry\r\n"
        "  echo update FAILED after 30s, abort\r\n"
        f'  del "{new_path}" 2>nul\r\n'
        "  exit /b 1\r\n"
        ")\r\n"
        f'start "" "{current_exe}"\r\n'
        f'del "{bat_path}"\r\n'
    )
```

## 约束
- 不动 `_stage_self_update` 的其他逻辑 (api 调用, signature 检查, etc.)
- 不动 `_is_onedir_install` 分支 (line 200 的 onedir skip)
- 不动 `creationflags=0x08 | 0x200 | 0x08000000`
- bat 编码保持 ascii (现状)
- 修改完后 read_file 一次确认改动正确, 再 finish

## 验收
- [ ] `_update_tmp` 字符串出现在文件中
- [ ] `:retry` 字符串出现在文件中  
- [ ] `RETRY LSS 30` 出现在文件中
- [ ] `python3 -c "import ast; ast.parse(open('bin/recorder_consumer_lite.py').read())"` 通过

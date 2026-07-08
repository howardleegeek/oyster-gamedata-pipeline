#!/usr/bin/env python3
"""
G252 · bin/i18n_zh_en_strings.py

Internationalization: zh-CN + zh-TW + en-US runtime string loader.
Covers tray menu, splash, privacy dashboard, error messages.
.po format compatible with future locale adds.
"""

import argparse
import gettext
import json
import logging
import os
import struct
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class I18NStringLoader:
    """Internationalization string loader for zh-CN, zh-TW, en-US."""
    
    SUPPORTED_LOCALES = ['zh_CN', 'zh_TW', 'en_US']
    DEFAULT_LOCALE = 'en_US'
    DOMAIN = 'messages'
    
    def __init__(self, locale_dir: Optional[Union[str, Path]] = None):
        self.locale_dir = Path(locale_dir) if locale_dir else None
        self.translations: Dict[str, gettext.GNUTranslations] = {}
        self.fallback_strings = self._get_fallback_strings()
        self.current_locale = self.DEFAULT_LOCALE
        self._load_translations()
    
    def _get_fallback_strings(self) -> Dict[str, Dict[str, str]]:
        """Built-in fallback strings for all supported locales."""
        return {
            'en_US': {
                'tray.show_window': 'Show Window', 'tray.hide_window': 'Hide Window',
                'tray.settings': 'Settings', 'tray.privacy_dashboard': 'Privacy Dashboard',
                'tray.about': 'About', 'tray.quit': 'Quit', 'tray.connection_status': 'Connection Status',
                'tray.connected': 'Connected', 'tray.disconnected': 'Disconnected', 'tray.connecting': 'Connecting...',
                'splash.loading': 'Loading...', 'splash.initializing': 'Initializing application',
                'splash.loading_modules': 'Loading modules', 'splash.starting_services': 'Starting services',
                'splash.ready': 'Ready', 'privacy.title': 'Privacy Dashboard', 'privacy.data_collection': 'Data Collection',
                'privacy.data_collection.description': 'Control what data is collected', 'privacy.analytics': 'Analytics',
                'privacy.analytics.description': 'Help improve the application by sharing usage statistics',
                'privacy.diagnostics': 'Diagnostics', 'privacy.diagnostics.description': 'Share diagnostic information to help fix issues',
                'privacy.save': 'Save Settings', 'privacy.reset': 'Reset to Defaults', 'privacy.enabled': 'Enabled',
                'privacy.disabled': 'Disabled', 'error.general': 'An error occurred', 'error.network': 'Network error',
                'error.network.description': 'Could not connect to the server', 'error.permission': 'Permission denied',
                'error.permission.description': 'You do not have permission to perform this action',
                'error.config': 'Configuration error', 'error.config.description': 'Invalid configuration file',
                'error.file_not_found': 'File not found', 'error.file_not_found.description': 'The specified file does not exist',
                'error.invalid_input': 'Invalid input', 'error.invalid_input.description': 'The provided input is not valid',
                'error.timeout': 'Operation timed out', 'error.timeout.description': 'The operation took too long to complete',
                'error.retry': 'Retry', 'error.cancel': 'Cancel', 'error.ok': 'OK',
            },
            'zh_CN': {
                'tray.show_window': '显示窗口', 'tray.hide_window': '隐藏窗口', 'tray.settings': '设置',
                'tray.privacy_dashboard': '隐私仪表板', 'tray.about': '关于', 'tray.quit': '退出',
                'tray.connection_status': '连接状态', 'tray.connected': '已连接', 'tray.disconnected': '已断开',
                'tray.connecting': '连接中...', 'splash.loading': '加载中...', 'splash.initializing': '正在初始化应用程序',
                'splash.loading_modules': '正在加载模块', 'splash.starting_services': '正在启动服务', 'splash.ready': '准备就绪',
                'privacy.title': '隐私仪表板', 'privacy.data_collection': '数据收集',
                'privacy.data_collection.description': '控制收集哪些数据', 'privacy.analytics': '分析',
                'privacy.analytics.description': '通过分享使用统计帮助改进应用程序', 'privacy.diagnostics': '诊断',
                'privacy.diagnostics.description': '分享诊断信息以帮助修复问题', 'privacy.save': '保存设置',
                'privacy.reset': '重置为默认值', 'privacy.enabled': '已启用', 'privacy.disabled': '已禁用',
                'error.general': '发生错误', 'error.network': '网络错误', 'error.network.description': '无法连接到服务器',
                'error.permission': '权限被拒绝', 'error.permission.description': '您没有执行此操作的权限',
                'error.config': '配置错误', 'error.config.description': '配置文件无效', 'error.file_not_found': '文件未找到',
                'error.file_not_found.description': '指定的文件不存在', 'error.invalid_input': '输入无效',
                'error.invalid_input.description': '提供的输入无效', 'error.timeout': '操作超时',
                'error.timeout.description': '操作耗时过长', 'error.retry': '重试', 'error.cancel': '取消', 'error.ok': '确定',
            },
            'zh_TW': {
                'tray.show_window': '顯示視窗', 'tray.hide_window': '隱藏視窗', 'tray.settings': '設定',
                'tray.privacy_dashboard': '隱私儀表板', 'tray.about': '關於', 'tray.quit': '退出',
                'tray.connection_status': '連線狀態', 'tray.connected': '已連線', 'tray.disconnected': '已斷線',
                'tray.connecting': '連線中...', 'splash.loading': '載入中...', 'splash.initializing': '正在初始化應用程式',
                'splash.loading_modules': '正在載入模組', 'splash.starting_services': '正在啟動服務', 'splash.ready': '準備就緒',
                'privacy.title': '隱私儀表板', 'privacy.data_collection': '資料收集',
                'privacy.data_collection.description': '控制收集哪些資料', 'privacy.analytics': '分析',
                'privacy.analytics.description': '透過分享使用統計幫助改進應用程式', 'privacy.diagnostics': '診斷',
                'privacy.diagnostics.description': '分享診斷資訊以幫助修復問題', 'privacy.save': '儲存設定',
                'privacy.reset': '重設為預設值', 'privacy.enabled': '已啟用', 'privacy.disabled': '已停用',
                'error.general': '發生錯誤', 'error.network': '網路錯誤', 'error.network.description': '無法連線到伺服器',
                'error.permission': '權限被拒絕', 'error.permission.description': '您沒有執行此操作的權限',
                'error.config': '設定錯誤', 'error.config.description': '設定檔案無效', 'error.file_not_found': '檔案未找到',
                'error.file_not_found.description': '指定的檔案不存在', 'error.invalid_input': '輸入無效',
                'error.invalid_input.description': '提供的輸入無效', 'error.timeout': '操作逾時',
                'error.timeout.description': '操作耗時過長', 'error.retry': '重試', 'error.cancel': '取消', 'error.ok': '確定',
            }
        }
    
    def _load_translations(self) -> None:
        """Load translations from .po/.mo files if available."""
        if not self.locale_dir or not self.locale_dir.exists():
            return
        
        for locale in self.SUPPORTED_LOCALES:
            mo_file = self.locale_dir / locale / 'LC_MESSAGES' / f'{self.DOMAIN}.mo'
            if mo_file.exists():
                try:
                    with open(mo_file, 'rb') as f:
                        self.translations[locale] = gettext.GNUTranslations(f)
                except (OSError, ValueError, struct.error) as exc:
                    logger.debug(
                        "Skipping unparseable .mo translation file %s: %s",
                        mo_file, exc, exc_info=True,
                    )
    
    def set_locale(self, locale: str) -> bool:
        """Set current locale. Returns True if successful."""
        locale = locale.replace('-', '_')
        if locale in self.SUPPORTED_LOCALES:
            self.current_locale = locale
            return True
        for supported_locale in self.SUPPORTED_LOCALES:
            if supported_locale.startswith(locale.split('_')[0]):
                self.current_locale = supported_locale
                return True
        self.current_locale = self.DEFAULT_LOCALE
        return False
    
    def get_locale(self) -> str:
        """Get current locale."""
        return self.current_locale
    
    def get_supported_locales(self) -> List[str]:
        """Get list of supported locales."""
        return self.SUPPORTED_LOCALES.copy()
    
    def translate(self, message_id: str, **kwargs) -> str:
        """Translate message ID to current locale."""
        # Try gettext translation
        if self.current_locale in self.translations:
            translated = self.translations[self.current_locale].gettext(message_id)
            if translated != message_id:
                try:
                    return translated.format(**kwargs) if kwargs else translated
                except (KeyError, ValueError) as exc:
                    logger.debug(
                        "gettext translation format failed for %r in locale %s: %s",
                        message_id, self.current_locale, exc,
                    )
                    return translated

        # Fall back to built-in strings
        if (self.current_locale in self.fallback_strings and
            message_id in self.fallback_strings[self.current_locale]):
            translated = self.fallback_strings[self.current_locale][message_id]
            try:
                return translated.format(**kwargs) if kwargs else translated
            except (KeyError, ValueError) as exc:
                logger.debug(
                    "fallback translation format failed for %r in locale %s: %s",
                    message_id, self.current_locale, exc,
                )
                return translated

        # Fall back to English
        if message_id in self.fallback_strings['en_US']:
            translated = self.fallback_strings['en_US'][message_id]
            try:
                return translated.format(**kwargs) if kwargs else translated
            except (KeyError, ValueError) as exc:
                logger.debug(
                    "en_US fallback translation format failed for %r: %s",
                    message_id, exc,
                )
                return translated
        
        # Return original message ID if no translation found
        return message_id.format(**kwargs) if kwargs else message_id
    
    def tray(self, message_id: str, **kwargs) -> str:
        """Get tray menu translation."""
        return self.translate(f'tray.{message_id}', **kwargs)
    
    def splash(self, message_id: str, **kwargs) -> str:
        """Get splash screen translation."""
        return self.translate(f'splash.{message_id}', **kwargs)
    
    def privacy(self, message_id: str, **kwargs) -> str:
        """Get privacy dashboard translation."""
        return self.translate(f'privacy.{message_id}', **kwargs)
    
    def error(self, message_id: str, **kwargs) -> str:
        """Get error message translation."""
        return self.translate(f'error.{message_id}', **kwargs)
    
    def export_strings(self, output_dir: Union[str, Path], format: str = 'po') -> None:
        """Export translation strings to files."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        for locale in self.SUPPORTED_LOCALES:
            locale_dir = output_dir / locale / 'LC_MESSAGES'
            locale_dir.mkdir(parents=True, exist_ok=True)
            
            if format == 'po':
                self._export_po(locale_dir, locale)
            elif format == 'json':
                self._export_json(locale_dir, locale)
    
    def _export_po(self, output_dir: Path, locale: str) -> None:
        """Export strings as .po file."""
        output_file = output_dir / f'{self.DOMAIN}.po'
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f'# {locale} translations\n')
            f.write('msgid ""\nmsgstr ""\n')
            f.write('"Content-Type: text/plain; charset=UTF-8\\n"\n')
            f.write(f'"Language: {locale}\\n"\n\n')
            
            for msgid in sorted(self.fallback_strings['en_US'].keys()):
                translation = self.fallback_strings.get(locale, {}).get(msgid, '')
                f.write(f'msgid "{msgid}"\nmsgstr "{translation}"\n\n')
    
    def _export_json(self, output_dir: Path, locale: str) -> None:
        """Export strings as JSON file."""
        output_file = output_dir / f'{self.DOMAIN}.json'
        translations = {}
        for msgid in self.fallback_strings['en_US'].keys():
            translations[msgid] = self.fallback_strings.get(locale, {}).get(
                msgid, self.fallback_strings['en_US'][msgid]
            )
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump({
                'locale': locale,
                'translations': translations
            }, f, ensure_ascii=False, indent=2)


# Global instance for easy access
_i18n_loader: Optional[I18NStringLoader] = None


def init_i18n(locale_dir: Optional[Union[str, Path]] = None) -> I18NStringLoader:
    """Initialize global i18n loader."""
    global _i18n_loader
    _i18n_loader = I18NStringLoader(locale_dir)
    return _i18n_loader


def get_i18n() -> I18NStringLoader:
    """Get global i18n loader instance."""
    global _i18n_loader
    if _i18n_loader is None:
        _i18n_loader = I18NStringLoader()
    return _i18n_loader


def set_locale(locale: str) -> bool:
    """Set current locale for global i18n loader."""
    return get_i18n().set_locale(locale)


def translate(message_id: str, **kwargs) -> str:
    """Translate message ID using global i18n loader."""
    return get_i18n().translate(message_id, **kwargs)


def tray(message_id: str, **kwargs) -> str:
    """Get tray menu translation."""
    return get_i18n().tray(message_id, **kwargs)


def splash(message_id: str, **kwargs) -> str:
    """Get splash screen translation."""
    return get_i18n().splash(message_id, **kwargs)


def privacy(message_id: str, **kwargs) -> str:
    """Get privacy dashboard translation."""
    return get_i18n().privacy(message_id, **kwargs)


def error(message_id: str, **kwargs) -> str:
    """Get error message translation."""
    return get_i18n().error(message_id, **kwargs)


def main(argv: Optional[List[str]] = None) -> int:
    """Command-line interface for i18n string management."""
    parser = argparse.ArgumentParser(description='Internationalization string loader and manager')
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # List command
    subparsers.add_parser('list', help='List supported locales')
    
    # Translate command
    translate_parser = subparsers.add_parser('translate', help='Translate a message')
    translate_parser.add_argument('message_id', help='Message ID to translate')
    translate_parser.add_argument('--locale', '-l', default='en_US', help='Locale to use')
    translate_parser.add_argument('--format-args', '-f', type=json.loads, default='{}', help='Format args as JSON')
    
    # Export command
    export_parser = subparsers.add_parser('export', help='Export translation strings')
    export_parser.add_argument('--output', '-o', required=True, help='Output directory')
    export_parser.add_argument('--format', '-f', choices=['po', 'json'], default='po', help='Output format')
    
    # Validate command
    validate_parser = subparsers.add_parser('validate', help='Validate translation files')
    validate_parser.add_argument('--locale-dir', '-d', required=True, help='Directory containing locale files')
    
    args = parser.parse_args(argv)
    
    if args.command == 'list':
        i18n = I18NStringLoader()
        print("Supported locales:")
        for locale in i18n.get_supported_locales():
            print(f"  - {locale}")
        return 0
    
    elif args.command == 'translate':
        i18n = I18NStringLoader()
        i18n.set_locale(args.locale)
        result = i18n.translate(args.message_id, **args.format_args)
        print(result)
        return 0
    
    elif args.command == 'export':
        i18n = I18NStringLoader()
        i18n.export_strings(args.output, args.format)
        print(f"Exported translations to {args.output} in {args.format} format")
        return 0
    
    elif args.command == 'validate':
        if not os.path.exists(args.locale_dir):
            print(f"Error: Directory does not exist: {args.locale_dir}")
            return 1
        
        i18n = I18NStringLoader(args.locale_dir)
        print(f"Validating locale directory: {args.locale_dir}")
        
        for locale in i18n.get_supported_locales():
            locale_path = Path(args.locale_dir) / locale / 'LC_MESSAGES'
            mo_file = locale_path / f'{i18n.DOMAIN}.mo'
            po_file = locale_path / f'{i18n.DOMAIN}.po'
            
            if mo_file.exists():
                print(f"  ✓ {locale}: Found .mo file")
            elif po_file.exists():
                print(f"  ✓ {locale}: Found .po file")
            else:
                print(f"  ✗ {locale}: No translation files found")
        
        return 0
    
    else:
        parser.print_help()
        return 0


if __name__ == '__main__':
    sys.exit(main())

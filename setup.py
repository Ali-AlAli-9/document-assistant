#!/usr/bin/env python
import os
import sys
import shutil
import subprocess
import urllib.request
import json
import re

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
elif os.name == 'nt':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, '.env')
ENV_EXAMPLE_PATH = os.path.join(BASE_DIR, '.env.example')
FRONTEND_PATH = os.path.join(BASE_DIR, 'frontend')


def print_header():
    print()
    print('=' * 52)
    print('   Document Assistant — معالج الإعداد التفاعلي')
    print('=' * 52)
    print()


def ensure_env():
    if os.path.exists(ENV_PATH):
        print('✅  .env موجود مسبقاً')
        return
    if not os.path.exists(ENV_EXAMPLE_PATH):
        print('❌  .env.example غير موجود')
        sys.exit(1)
    shutil.copy(ENV_EXAMPLE_PATH, ENV_PATH)
    print('✅  تم إنشاء .env من .env.example')


def read_env():
    env = {}
    if not os.path.exists(ENV_PATH):
        return env
    with open(ENV_PATH, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, value = line.partition('=')
                env[key.strip()] = value.strip().strip('"\'')
    return env


def write_env(updates):
    if not os.path.exists(ENV_PATH):
        print('❌  .env غير موجود')
        return
    with open(ENV_PATH, encoding='utf-8') as f:
        lines = f.readlines()
    updated = set()
    out = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith('#') and '=' in s:
            key = s.split('=', 1)[0].strip()
            if key in updates:
                out.append(f'{key}={updates[key]}\n')
                updated.add(key)
            else:
                out.append(line)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in updated:
            out.append(f'{key}={value}\n')
    with open(ENV_PATH, 'w', encoding='utf-8') as f:
        f.writelines(out)
    print(f'✅  تم تحديث .env')


def run_cmd(cmd, cwd=None):
    try:
        r = subprocess.run(cmd, cwd=cwd or BASE_DIR,
                           capture_output=True, text=True, shell=True)
        return r.returncode == 0, r.stdout.strip()
    except FileNotFoundError:
        return False, ''


def test_gemini_key(api_key):
    try:
        url = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent'
        data = json.dumps({
            'contents': [{'parts': [{'text': 'ok'}]}]
        }).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={
                'Content-Type': 'application/json',
                'x-goog-api-key': api_key,
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception:
        return False


def setup_ollama():
    print()
    print('── Ollama ──────────────────────────────')
    ok, _ = run_cmd('ollama --version')
    if not ok:
        print('⚠️  Ollama غير مثبت')
        print('   حمّله من: https://ollama.com')
        if input('   بعد التثبيت، اكتب y للمتابعة: ').strip().lower() != 'y':
            return False
        ok, _ = run_cmd('ollama --version')
        if not ok:
            print('❌  لم يتم العثور على Ollama')
            return False
    model = 'llama3.2:3b'
    ok, out = run_cmd('ollama list')
    if model not in out:
        print(f'⚠️  النموذج {model} غير موجود')
        if input('   هل تريد سحبه الآن؟ (y/n): ').strip().lower() == 'y':
            print('📥  جاري سحب النموذج ...')
            run_cmd(f'ollama pull {model}')
    write_env({'LLM_PROVIDER': 'ollama', 'OLLAMA_MODEL': model})
    print('✅  تم ضبط Ollama')
    return True


def setup_gemini():
    print()
    print('── Gemini ──────────────────────────────')
    print('   احصل على مفتاح API من:')
    print('   https://aistudio.google.com')
    print('   (مجاني 1500 req/day — يحتاج حساب Gmail)')
    print()
    for i in range(3):
        key = input('   أدخل مفتاح Gemini API: ').strip()
        if not key:
            print('❌  المفتاح فارغ')
            continue
        print('   جاري اختبار المفتاح...')
        if test_gemini_key(key):
            print('✅  المفتاح صحيح!')
            write_env({
                'LLM_PROVIDER': 'gemini',
                'GEMINI_API_KEY': key,
                'GEMINI_MODEL': 'gemini-2.5-flash',
            })
            return True
        print('❌  المفتاح غير صالح أو فشل الاتصال')
        if i < 2 and input('   إدخال مفتاح آخر؟ (y/n): ').strip().lower() != 'y':
            break
    return False


def run_migrations():
    print()
    print('── قاعدة البيانات ──────────────────────')
    ok, out = run_cmd(f'"{sys.executable}" manage.py migrate')
    if ok:
        print('✅  تم تشغيل الترحيلات')
    else:
        print(f'❌  فشلت الترحيلات: {out}')


def build_frontend():
    dist = os.path.join(FRONTEND_PATH, 'dist')
    if os.path.isdir(dist):
        print('✅  الواجهة الأمامية مبنية مسبقاً')
        return
    print()
    print('── بناء الواجهة الأمامية ───────────────')
    if not os.path.isdir(os.path.join(FRONTEND_PATH, 'node_modules')):
        print('📦  تثبيت الاعتماديات...')
        run_cmd('npm install', cwd=FRONTEND_PATH)
    print('🔨  جاري البناء...')
    ok, _ = run_cmd('npm run build', cwd=FRONTEND_PATH)
    if ok:
        print('✅  تم بناء الواجهة')
    else:
        print('⚠️  فشل البناء. شغّل يدوياً: cd frontend && npm run build')


def main():
    print_header()
    ensure_env()
    print()
    print('اختر مزود LLM:')
    print('  1) Ollama  — محلي، مجاني، خصوصية تامة (~2GB)')
    print('  2) Gemini  — سحابي، مجاني 1500 req/day')
    choice = input('\nادخل 1 أو 2: ').strip()
    success = False
    if choice == '1':
        success = setup_ollama()
    elif choice == '2':
        success = setup_gemini()
    else:
        print('❌  اختيار غير صحيح')
        sys.exit(1)
    if not success:
        print('\n⚠️  لم يكتمل الإعداد. يمكنك تعديل .env يدوياً')
        sys.exit(1)
    run_migrations()
    build_frontend()
    print()
    print('=' * 52)
    print('   ✅  تم الإعداد بنجاح!')
    print('=' * 52)
    print()
    print('   شغّل الخادم:')
    print(f'      {sys.executable} manage.py runserver 8000')
    print()
    print('   ثم افتح:  http://localhost:8000')
    print()


if __name__ == '__main__':
    main()

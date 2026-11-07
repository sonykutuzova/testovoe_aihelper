const form = document.getElementById('form');
const input = document.getElementById('input');
const output = document.getElementById('output');
const generate = document.getElementById('generate');
const copy = document.getElementById('copy');
const error = document.getElementById('error');
const status = document.getElementById('status');
let result = '';

form.addEventListener('submit', async event => {
  event.preventDefault();
  error.hidden = true;
  status.textContent = '';
  result = '';
  copy.disabled = true;
  output.textContent = 'Готовим описание…';
  generate.disabled = true;
  try {
    try { JSON.parse(input.value); }
    catch { throw new Error('Некорректный JSON. Проверьте кавычки, запятые и скобки.'); }
    const response = await fetch('/generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: input.value,
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error([data.error?.message || 'Ошибка сервера.', ...(data.error?.details || [])].join('\n'));
    }
    result = JSON.stringify(data, null, 2);
    output.textContent = result;
    copy.disabled = false;
    status.textContent = 'Готово. Перед публикацией сверьте текст с паспортом.';
  } catch (e) {
    error.textContent = e.message || 'Не удалось получить ответ сервера.';
    error.hidden = false;
    output.textContent = 'Карточка не сформирована.';
  } finally {
    generate.disabled = false;
  }
});

copy.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(result);
    status.textContent = 'JSON скопирован.';
  } catch {
    status.textContent = 'Не удалось скопировать. Выделите текст результата и скопируйте вручную.';
  }
});

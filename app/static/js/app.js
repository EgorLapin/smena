/* NFR-6: блокировка кнопки при отправке формы (защита от двойного клика на UI;
   серверная защита от повторов — AL-2/AL-3). */
document.addEventListener("submit", function (event) {
  var form = event.target;
  if (!form.checkValidity || !form.checkValidity()) return;
  var buttons = form.querySelectorAll("button[type=submit], input[type=submit]");
  for (var i = 0; i < buttons.length; i++) {
    var b = buttons[i];
    b.dataset.label = b.value || b.textContent;
    b.disabled = true;
    if (b.value) { b.value = "Сохраняю..."; } else { b.textContent = "Сохраняю..."; }
  }
});

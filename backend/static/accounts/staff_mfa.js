document.addEventListener("DOMContentLoaded", () => {
  const root = document.getElementById("staff-mfa");
  if (!root) return;

  const byId = (id) => document.getElementById(id);
  const panels = ["mfa-enroll-start", "mfa-enroll-confirm", "mfa-challenge", "mfa-recovery-panel", "mfa-recovery-result", "mfa-ready"];
  const statusText = byId("mfa-status");
  const errorBox = byId("mfa-errors");
  let csrf = document.querySelector('input[name="csrfmiddlewaretoken"]')?.value || "";
  let currentStatus = null;

  function showPanel(id) {
    panels.forEach((panel) => { byId(panel).hidden = panel !== id; });
  }
  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
  }
  function clearError() {
    errorBox.textContent = "";
    errorBox.hidden = true;
  }
  function errorMessage(code) {
    const messages = {
      authentication_required: "Sua sessão expirou. Entre novamente.",
      permission_denied: "Esta área é restrita à equipe do evento.",
      invalid_credentials: "A senha informada não confere.",
      invalid_mfa_code: "Código inválido, expirado ou já utilizado. Confira e tente novamente.",
      mfa_required: "Confirme sua identidade para continuar.",
      mfa_enrollment_required: "Configure a autenticação em duas etapas para acessar a gestão.",
      mfa_enrollment_expired: "A configuração expirou. Inicie novamente.",
      mfa_mandatory: "A política do evento exige manter a autenticação ativa.",
      rate_limited: "Muitas tentativas. Aguarde um pouco antes de tentar novamente.",
      csrf_failed: "A sessão de segurança mudou. Atualize a página e tente novamente.",
      operation_unavailable: "O serviço está temporariamente indisponível. Tente mais tarde.",
      mfa_unavailable: "Não foi possível validar o autenticador. Procure o suporte técnico."
    };
    return messages[code] || "Não foi possível concluir a operação. Tente novamente.";
  }
  async function api(url, method = "GET", body = null) {
    const options = { method, credentials: "same-origin", headers: { "Accept": "application/json" } };
    if (body !== null) {
      options.headers["Content-Type"] = "application/json";
      options.headers["X-CSRFToken"] = csrf;
      options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok !== true) {
      throw new Error(payload?.error?.code || "operation_unavailable");
    }
    if (payload.data?.csrf_token) csrf = payload.data.csrf_token;
    return payload.data || {};
  }
  async function loadStatus() {
    clearError();
    try {
      currentStatus = await api(root.dataset.statusUrl);
      statusText.textContent = currentStatus.enabled
        ? "Autenticação em duas etapas já configurada."
        : currentStatus.required
          ? "A autenticação em duas etapas é necessária para acessar a gestão."
          : "A autenticação em duas etapas ainda não está configurada.";
      if (!currentStatus.enabled) showPanel("mfa-enroll-start");
      else if (!currentStatus.verified) showPanel("mfa-challenge");
      else {
        showPanel("mfa-ready");
        byId("mfa-recovery-count").textContent = "Códigos de recuperação restantes: " + currentStatus.recovery_codes_remaining + ".";
      }
    } catch (error) {
      showPanel(null);
      showError(errorMessage(error.message));
    }
  }
  async function submit(formId, action) {
    clearError();
    const form = byId(formId);
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try { await action(); }
    catch (error) { showError(errorMessage(error.message)); }
    finally { button.disabled = false; }
  }

  byId("mfa-enroll-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submit("mfa-enroll-form", async () => {
      const data = await api(root.dataset.enrollUrl, "POST", { password: byId("mfa-enroll-password").value });
      byId("mfa-secret").value = data.secret;
      const link = byId("mfa-otpauth");
      link.href = data.provisioning_uri;
      link.textContent = "Abrir URI no aplicativo compatível";
      byId("mfa-enroll-password").value = "";
      statusText.textContent = "A configuração expira em dez minutos. Confirme com o aplicativo.";
      showPanel("mfa-enroll-confirm");
    });
  });
  byId("mfa-confirm-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submit("mfa-confirm-form", async () => {
      const data = await api(root.dataset.confirmUrl, "POST", { code: byId("mfa-confirm-code").value });
      byId("mfa-confirm-code").value = "";
      renderRecovery(data.recovery_codes || []);
    });
  });
  byId("mfa-verify-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submit("mfa-verify-form", async () => {
      await api(root.dataset.verifyUrl, "POST", {
        code: byId("mfa-verify-code").value.trim(),
        method: byId("mfa-verify-method").value
      });
      byId("mfa-verify-code").value = "";
      // A successful response means the server granted the MFA proof.
      window.location.assign(root.dataset.managementUrl);
    });
  });
  byId("mfa-recovery-open").addEventListener("click", () => {
    clearError();
    showPanel("mfa-recovery-panel");
  });
  byId("mfa-recovery-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submit("mfa-recovery-form", async () => {
      const data = await api(root.dataset.recoveryUrl, "POST", { password: byId("mfa-recovery-password").value });
      byId("mfa-recovery-password").value = "";
      renderRecovery(data.recovery_codes || []);
    });
  });
  function renderRecovery(codes) {
    const list = byId("mfa-recovery-list");
    list.replaceChildren();
    codes.forEach((code) => {
      const item = document.createElement("li");
      const value = document.createElement("code");
      value.textContent = code;
      item.appendChild(value);
      list.appendChild(item);
    });
    statusText.textContent = "Guarde os códigos de recuperação em um local privado.";
    showPanel("mfa-recovery-result");
  }
  byId("mfa-recovery-done").addEventListener("click", async () => {
    await loadStatus();
    if (currentStatus?.verified) window.location.assign(root.dataset.managementUrl);
  });
  loadStatus();
});

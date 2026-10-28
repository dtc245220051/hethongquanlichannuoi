document.addEventListener("DOMContentLoaded", () => {
  const shell = document.querySelector("[data-sidebar]");
  const toggle = document.querySelector("[data-sidebar-toggle]");
  toggle?.addEventListener("click", () => {
    const mobile = window.matchMedia("(max-width: 760px)").matches;
    const activeClass = mobile ? "sidebar-open" : "sidebar-collapsed";
    const expanded = shell.classList.toggle(activeClass);
    toggle.setAttribute("aria-expanded", String(mobile ? expanded : !expanded));
  });

  document.addEventListener(
    "submit",
    (event) => {
      const form = event.target;
      if (!(form instanceof HTMLFormElement) || !form.dataset.confirm) return;
      if (!window.confirm(form.dataset.confirm)) {
        event.preventDefault();
        event.stopPropagation();
      }
    },
    true
  );

  document.querySelectorAll("[data-target-kind]").forEach((targetKind) => {
    const form = targetKind.closest("form");
    const herdTarget = form?.querySelector("[data-herd-target]");
    const animalTarget = form?.querySelector("[data-animal-target]");
    const updateTarget = () => {
      if (!herdTarget || !animalTarget) return;
      const animalSelected = targetKind.value === "animal";
      herdTarget.hidden = animalSelected;
      animalTarget.hidden = !animalSelected;
      herdTarget.querySelector("select").disabled = animalSelected;
      animalTarget.querySelector("select").disabled = !animalSelected;
    };
    targetKind.addEventListener("change", updateTarget);
    updateTarget();
  });

  document.querySelectorAll(".outcome-form").forEach((form) => {
    const outcome = form.querySelector('[name="outcome"]');
    const occurred = form.querySelector('[name="occurred_at"]');
    const updateRequired = () => {
      occurred.required = outcome.value === "administered";
    };
    outcome.addEventListener("change", updateRequired);
    updateRequired();
  });

  const assistantForm = document.querySelector("#assistant-form");
  if (assistantForm) {
    const input = assistantForm.querySelector("#assistant-question");
    const sendButton = assistantForm.querySelector('button[type="submit"]');
    const progress = assistantForm.querySelector("[data-submit-progress]");
    const messages = document.querySelector("[data-chat-messages]");
    const errorBox = document.querySelector("[data-chat-error]");
    const clearButton = document.querySelector("[data-clear-chat]");
    const storageKey = assistantForm.dataset.chatKey;
    const maxMessages = 8;

    const readHistory = () => {
      try {
        const history = JSON.parse(sessionStorage.getItem(storageKey) || "[]");
        if (!Array.isArray(history) || history.length > maxMessages) return [];
        let expectedRole = "user";
        let totalCharacters = 0;
        for (const message of history) {
          if (
            !message ||
            Object.keys(message).length !== 2 ||
            message.role !== expectedRole ||
            typeof message.content !== "string" ||
            !message.content.trim() ||
            message.content.length > 4000
          ) return [];
          totalCharacters += message.content.length;
          if (totalCharacters > 12000) return [];
          expectedRole = expectedRole === "user" ? "assistant" : "user";
        }
        return history.length % 2 === 0 ? history : [];
      } catch {
        return [];
      }
    };

    const addMessage = (role, content, metadata = null) => {
      const article = document.createElement("article");
      article.className = `assistant-chat-message assistant-chat-${role}`;
      const label = document.createElement("strong");
      label.className = "assistant-chat-author";
      label.textContent = role === "user" ? "Bạn" : (metadata?.mode_label || "Trợ lý");
      const body = document.createElement("p");
      body.className = "assistant-chat-content";
      body.textContent = content;
      article.append(label, body);

      if (metadata?.data_scope) {
        const scope = document.createElement("small");
        scope.className = "assistant-scope";
        scope.textContent = metadata.data_scope;
        article.append(scope);
      }
      if (Array.isArray(metadata?.tool_calls) && metadata.tool_calls.length) {
        for (const call of metadata.tool_calls) {
          const details = document.createElement("details");
          details.className = "assistant-chat-tool";
          const summary = document.createElement("summary");
          summary.textContent = `${call.name} · ${call.row_count} bản ghi`;
          const description = document.createElement("p");
          description.textContent = call.description;
          const data = document.createElement("pre");
          data.textContent = JSON.stringify(call.data, null, 2);
          details.append(summary, description, data);
          article.append(details);
        }
      }
      messages.append(article);
      messages.scrollTop = messages.scrollHeight;
    };

    const renderHistory = (history) => {
      messages.replaceChildren();
      for (const message of history) addMessage(message.role, message.content);
    };

    let history = readHistory();
    renderHistory(history);

    clearButton?.addEventListener("click", () => {
      history = [];
      sessionStorage.removeItem(storageKey);
      messages.replaceChildren();
      errorBox.hidden = true;
      input.focus();
    });

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        assistantForm.requestSubmit();
      }
    });

    assistantForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const question = input.value.trim();
      if (!question || sendButton.disabled) return;
      if (question.length > 2000) {
        errorBox.textContent = "Câu hỏi vượt giới hạn 2.000 ký tự.";
        errorBox.hidden = false;
        return;
      }

      errorBox.hidden = true;
      renderHistory(history);
      addMessage("user", question);
      sendButton.disabled = true;
      sendButton.setAttribute("aria-busy", "true");
      sendButton.dataset.originalText = sendButton.textContent;
      sendButton.textContent = assistantForm.dataset.loadingText || "Đang gửi…";
      progress.hidden = false;

      try {
        const response = await fetch(assistantForm.dataset.messageUrl, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": assistantForm.dataset.csrfToken,
            "Accept": "application/json",
          },
          body: JSON.stringify({ message: question, history }),
        });
        let payload = null;
        try {
          payload = await response.json();
        } catch {
          throw new Error("Phản hồi không hợp lệ. Hãy tải lại trang rồi thử lại.");
        }
        if (!response.ok || !payload.ok || !payload.message) {
          throw new Error(payload.error || "Không thể gửi yêu cầu. Vui lòng thử lại.");
        }
        const reply = payload.message;
        addMessage("assistant", reply.content, reply);
        history = [
          ...history,
          { role: "user", content: question },
          { role: "assistant", content: reply.content.slice(0, 4000) },
        ].slice(-maxMessages);
        while (
          history.reduce((total, message) => total + message.content.length, 0) > 12000
        ) {
          history.splice(0, 2);
        }
        try {
          sessionStorage.setItem(storageKey, JSON.stringify(history));
        } catch {
          errorBox.textContent = "Tin nhắn đã được trả lời nhưng không thể lưu ngữ cảnh trong phiên trình duyệt.";
          errorBox.hidden = false;
        }
        input.value = "";
      } catch (error) {
        errorBox.textContent = error instanceof Error
          ? error.message
          : "Không thể kết nối trợ lý. Vui lòng thử lại.";
        errorBox.hidden = false;
      } finally {
        sendButton.disabled = false;
        sendButton.removeAttribute("aria-busy");
        sendButton.textContent = sendButton.dataset.originalText || "Tra cứu";
        progress.hidden = true;
        input.focus();
      }
    });
  }

  document.querySelectorAll('form[method="post"]').forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (event.defaultPrevented || form === assistantForm) return;
      const button = form.querySelector('button[type="submit"], button:not([type])');
      if (button && !button.disabled) {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        button.dataset.originalText = button.textContent;
        button.textContent = form.dataset.loadingText || "Đang lưu…";
        const progress = form.querySelector("[data-submit-progress]");
        if (progress) progress.hidden = false;
      }
    });
  });

  const feedRows = document.querySelector("[data-feed-rows]");
  const addFeedRow = document.querySelector("[data-add-feed-row]");
  if (feedRows && addFeedRow) {
    const addRow = () => {
      const rows = feedRows.querySelectorAll("[data-feed-row]");
      const clone = rows[rows.length - 1].cloneNode(true);
      clone.querySelectorAll("input").forEach((input) => {
        input.value = input.name === "unit" ? "kg" : input.name === "price_per_unit" ? "0" : "";
      });
      feedRows.append(clone);
      clone.querySelector('input[name="feed_name"]')?.focus();
    };
    addFeedRow.addEventListener("click", addRow);
    feedRows.addEventListener("click", (event) => {
      const removeButton = event.target.closest("[data-remove-feed-row]");
      if (!removeButton) return;
      const rows = feedRows.querySelectorAll("[data-feed-row]");
      if (rows.length <= 1) {
        rows[0].querySelectorAll("input").forEach((input) => {
          input.value = input.name === "unit" ? "kg" : input.name === "price_per_unit" ? "0" : "";
        });
        return;
      }
      removeButton.closest("[data-feed-row]").remove();
    });
  }

  const growthCanvas = document.querySelector("#growth-chart");
  const growthData = document.querySelector("#growth-chart-data");
  if (growthCanvas instanceof HTMLCanvasElement && growthData) {
    try {
      const payload = JSON.parse(growthData.textContent || "{}");
      drawGrowthChart(growthCanvas, payload.points || [], payload.forecast || []);
    } catch {
      growthCanvas.hidden = true;
    }
  }

  function drawGrowthChart(canvas, points, forecast) {
    if (!points.length) {
      canvas.hidden = true;
      return;
    }
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth || 720;
    const height = 260;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const padding = { top: 18, right: 18, bottom: 34, left: 52 };
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;
    const all = [...points, ...forecast];
    const xs = all.map((point) => point.x);
    const ys = all.map((point) => point.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs) || minX + 1;
    const minY = Math.max(0, Math.min(...ys) * 0.95);
    const maxY = Math.max(...ys) * 1.05 || minY + 1;
    const scaleX = (value) =>
      padding.left + ((value - minX) / (maxX - minX || 1)) * plotWidth;
    const scaleY = (value) =>
      padding.top + plotHeight - ((value - minY) / (maxY - minY || 1)) * plotHeight;

    ctx.strokeStyle = "#e4eae6";
    ctx.fillStyle = "#78867e";
    ctx.font = "10px Inter, 'Segoe UI', Arial, sans-serif";
    ctx.lineWidth = 1;
    for (let step = 0; step <= 4; step += 1) {
      const value = minY + ((maxY - minY) * step) / 4;
      const y = scaleY(value);
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();
      ctx.fillText(`${value.toFixed(1)} kg`, 4, y + 3);
    }

    const drawSeries = (series, color, dashed) => {
      if (!series.length) return;
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      if (dashed) ctx.setLineDash([6, 4]);
      ctx.beginPath();
      series.forEach((point, index) => {
        const x = scaleX(point.x);
        const y = scaleY(point.y);
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.restore();
    };

    const last = points[points.length - 1];
    drawSeries(points, "#287149", false);
    drawSeries([last, ...forecast], "#a96a12", true);

    ctx.fillStyle = "#287149";
    for (const point of points) {
      ctx.beginPath();
      ctx.arc(scaleX(point.x), scaleY(point.y), 3, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.fillStyle = "#a96a12";
    for (const point of forecast) {
      ctx.beginPath();
      ctx.arc(scaleX(point.x), scaleY(point.y), 3, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.fillStyle = "#718078";
    ctx.fillText("● Lịch sử", padding.left, height - 10);
    ctx.fillStyle = "#a96a12";
    ctx.fillText("◌ Dự báo", padding.left + 80, height - 10);
  }
});

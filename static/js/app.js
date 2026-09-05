/* ---------------------------------------------------------------- */
/* Small helpers                                                     */
/* ---------------------------------------------------------------- */
const DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTH_ABBR = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

function $(sel, root = document) { return root.querySelector(sel); }
function $all(sel, root = document) { return [...root.querySelectorAll(sel)]; }
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of [].concat(children)) {
    if (child) node.append(child.nodeType ? child : document.createTextNode(child));
  }
  return node;
}
function fmtDate(dateStr) {
  const [y, m, d] = dateStr.split("-").map(Number);
  return { day: String(d).padStart(2, "0"), mon: MONTH_ABBR[m - 1] };
}
function toast(message, type = "info") {
  const stack = $("#toast-stack");
  const node = el("div", { class: `toast ${type}` }, message);
  stack.append(node);
  setTimeout(() => node.remove(), 4000);
}
function todayISO() {
  const d = new Date();
  return d.toISOString().slice(0, 10);
}

/* ---------------------------------------------------------------- */
/* Auth screen                                                       */
/* ---------------------------------------------------------------- */
function renderAuthScreen() {
  $("#auth-screen").classList.remove("hidden");
  $("#app-screen").classList.add("hidden");

  let mode = "login";
  const msgBox = $("#auth-msg");

  function setMode(next) {
    mode = next;
    $all(".tab").forEach((t) => t.classList.toggle("active", t.dataset.mode === mode));
    $("#field-name").classList.toggle("hidden", mode !== "register");
    $("#field-role").classList.toggle("hidden", mode !== "register");
    $("#field-specialty").classList.toggle("hidden", !(mode === "register" && getSelectedRole() === "provider"));
    $("#auth-submit").textContent = mode === "login" ? "Sign in" : "Create account";
    msgBox.classList.add("hidden");
  }
  function getSelectedRole() {
    const checked = $all('input[name="role"]').find((r) => r.checked);
    return checked ? checked.value : "client";
  }

  $all(".tab").forEach((t) => t.addEventListener("click", () => setMode(t.dataset.mode)));
  $all('input[name="role"]').forEach((r) =>
    r.addEventListener("change", () =>
      $("#field-specialty").classList.toggle("hidden", !(mode === "register" && getSelectedRole() === "provider"))
    )
  );

  $("#auth-form").onsubmit = async (e) => {
    e.preventDefault();
    msgBox.classList.add("hidden");
    const name = $("#input-name").value.trim();
    const email = $("#input-email").value.trim();
    const password = $("#input-password").value;

    try {
      let data;
      if (mode === "login") {
        data = await Api.post("/api/auth/login", { email, password });
      } else {
        data = await Api.post("/api/auth/register", {
          name, email, password,
          role: getSelectedRole(),
          specialty: $("#input-specialty").value.trim(),
        });
      }
      Api.setSession(data.token, data.user);
      bootApp();
    } catch (err) {
      msgBox.textContent = err.message;
      msgBox.classList.remove("hidden");
    }
  };

  setMode("login");
}

/* ---------------------------------------------------------------- */
/* App shell                                                          */
/* ---------------------------------------------------------------- */
const NAV_BY_ROLE = {
  client: [
    { id: "book", label: "Book an appointment" },
    { id: "my-appointments", label: "My appointments" },
  ],
  provider: [
    { id: "schedule", label: "My schedule" },
    { id: "appointments", label: "Appointments" },
    { id: "offerings", label: "What I offer" },
    { id: "coffee", label: "Coffee chats" },
  ],
  admin: [
    { id: "users", label: "Users" },
    { id: "all-appointments", label: "All appointments" },
    { id: "emails", label: "Email log" },
  ],
};

function renderAppScreen(user) {
  $("#auth-screen").classList.add("hidden");
  $("#app-screen").classList.remove("hidden");

  $("#who-name").textContent = user.name;
  const badge = $("#who-role");
  badge.textContent = user.role;
  badge.className = `role-badge ${user.role}`;

  const nav = $("#sidebar-nav");
  nav.innerHTML = "";
  const items = NAV_BY_ROLE[user.role] || [];
  items.forEach((item, idx) => {
    const node = el("div", { class: `nav-item${idx === 0 ? " active" : ""}`, onclick: () => selectView(item.id) }, [
      el("span", { class: "dot" }),
      item.label,
    ]);
    node.dataset.view = item.id;
    nav.append(node);
  });

  $("#logout-btn").onclick = () => {
    Api.clearSession();
    location.reload();
  };

  if (items.length) selectView(items[0].id);
}

function selectView(viewId) {
  $all(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.view === viewId));
  const renderers = {
    "book": renderBookView,
    "my-appointments": renderClientAppointmentsView,
    "schedule": renderProviderScheduleView,
    "appointments": renderProviderAppointmentsView,
    "offerings": renderOfferingsView,
    "coffee": renderCoffeeView,
    "users": renderAdminUsersView,
    "all-appointments": renderAdminAppointmentsView,
    "emails": renderAdminEmailsView,
  };
  (renderers[viewId] || (() => {}))();
}

function mainRoot() { return $("#main-content"); }

/* ---------------------------------------------------------------- */
/* CLIENT: Book an appointment                                       */
/* ---------------------------------------------------------------- */
async function renderBookView() {
  const root = mainRoot();
  root.innerHTML = "";
  root.append(
    el("h2", {}, "Book an appointment"),
    el("p", { class: "lede" }, "Pick a provider, choose a date, then claim an open slot.")
  );

  let providers = [];
  try {
    providers = (await Api.get("/api/providers")).providers;
  } catch (err) {
    root.append(el("div", { class: "form-msg error" }, err.message));
    return;
  }

  const listCard = el("div", { class: "card" }, [el("h3", {}, "Providers")]);
  const listWrap = el("div", { class: "provider-list" });
  listCard.append(listWrap);
  root.append(listCard);

  const boardCard = el("div", { class: "card hidden", id: "board-card" });
  root.append(boardCard);

  let selectedProvider = null;
  let selectedSlot = null;

  function renderProviders() {
    listWrap.innerHTML = "";
    if (!providers.length) {
      listWrap.append(el("div", { class: "empty-state" }, [
        el("div", { class: "glyph" }, "🗓"),
        "No providers have joined yet.",
      ]));
      return;
    }
    providers.forEach((p) => {
      const card = el("div", { class: `provider-card${selectedProvider?.id === p.id ? " selected" : ""}` }, [
        el("div", { class: "name" }, p.name),
        el("div", { class: "specialty" }, p.specialty || "General availability"),
      ]);
      card.onclick = () => { selectedProvider = p; selectedSlot = null; renderProviders(); renderBoard(); };
      listWrap.append(card);
    });
  }

  async function renderBoard() {
    if (!selectedProvider) { boardCard.classList.add("hidden"); return; }
    boardCard.classList.remove("hidden");
    boardCard.innerHTML = "";
    boardCard.append(el("h3", {}, `Availability — ${selectedProvider.name}`));

    const dateField = el("div", { class: "field" }, [
      el("label", {}, "Date"),
      (() => {
        const input = el("input", { type: "date", id: "book-date", value: todayISO() });
        input.min = todayISO();
        return input;
      })(),
    ]);
    boardCard.append(dateField);

    const board = el("div", { class: "board" }, [
      el("div", { class: "board-head" }, [el("span", {}, "Departures"), el("span", { id: "board-date-label" }, "")]),
      el("div", { class: "board-grid", id: "board-grid" }),
    ]);
    boardCard.append(board);

    const confirmRow = el("div", { style: "margin-top:1rem; display:flex; gap:0.75rem; align-items:center;" }, [
      el("button", { class: "btn btn-primary", id: "confirm-book-btn", disabled: "true" }, "Confirm booking"),
      el("span", { id: "confirm-hint", style: "color:var(--muted); font-size:0.85rem;" }, "Pick a slot above"),
    ]);
    boardCard.append(confirmRow);

    async function loadSlots() {
      const date = $("#book-date").value;
      $("#board-date-label").textContent = date;
      const grid = $("#board-grid");
      grid.innerHTML = '<div class="board-empty">Loading…</div>';
      try {
        const data = await Api.get(`/api/providers/${selectedProvider.id}/slots?date=${date}`);
        grid.innerHTML = "";
        if (!data.slots.length) {
          grid.append(el("div", { class: "board-empty" }, "No open slots on this date."));
          return;
        }
        data.slots.forEach((s) => {
          const chip = el("div", { class: "slot-chip" }, s.start);
          chip.onclick = () => {
            selectedSlot = s;
            $all(".slot-chip", grid).forEach((c) => c.classList.remove("selected"));
            chip.classList.add("selected");
            $("#confirm-book-btn").disabled = false;
            $("#confirm-hint").textContent = `${s.start} – ${s.end}`;
          };
          grid.append(chip);
        });
      } catch (err) {
        grid.innerHTML = "";
        grid.append(el("div", { class: "board-empty" }, err.message));
      }
    }

    $("#book-date").onchange = () => { selectedSlot = null; $("#confirm-book-btn").disabled = true; loadSlots(); };
    $("#confirm-book-btn").onclick = async () => {
      if (!selectedSlot) return;
      try {
        await Api.post("/api/appointments", {
          provider_id: selectedProvider.id,
          date: $("#book-date").value,
          start_time: selectedSlot.start,
          end_time: selectedSlot.end,
        });
        toast("Appointment booked.", "success");
        selectedSlot = null;
        loadSlots();
      } catch (err) {
        toast(err.message, "error");
        loadSlots();
      }
    };

    loadSlots();
  }

  renderProviders();
}

/* ---------------------------------------------------------------- */
/* CLIENT: My appointments                                            */
/* ---------------------------------------------------------------- */
async function renderClientAppointmentsView() {
  const root = mainRoot();
  root.innerHTML = "";
  root.append(el("h2", {}, "My appointments"), el("p", { class: "lede" }, "Everything you've booked, past and upcoming."));

  const card = el("div", { class: "card" });
  root.append(card);

  try {
    const { appointments } = await Api.get("/api/appointments/mine");
    if (!appointments.length) {
      card.append(el("div", { class: "empty-state" }, [el("div", { class: "glyph" }, "🎫"), "No appointments yet — go book one."]));
      return;
    }
    const list = el("div", { class: "ticket-list" });
    appointments.forEach((a) => {
      const { day, mon } = fmtDate(a.date);
      const ticket = el("div", { class: "ticket" }, [
        el("div", { class: "date-block" }, [el("div", { class: "day" }, day), el("div", { class: "mon" }, mon)]),
        el("div", { class: "info" }, [
          el("div", { class: "who" }, a.providerName),
          el("div", { class: "when" }, `${a.startTime} – ${a.endTime}`),
          el("span", { class: `status-pill ${a.status}` }, a.status),
        ]),
        el("div", { class: "actions" }, a.status === "confirmed" ? [
          (() => {
            const btn = el("button", { class: "btn btn-danger" }, "Cancel");
            btn.onclick = async () => {
              try { await Api.post(`/api/appointments/${a.id}/cancel`); toast("Appointment cancelled."); renderClientAppointmentsView(); }
              catch (err) { toast(err.message, "error"); }
            };
            return btn;
          })(),
        ] : []),
      ]);
      list.append(ticket);
    });
    card.append(list);
  } catch (err) {
    card.append(el("div", { class: "form-msg error" }, err.message));
  }
}

/* ---------------------------------------------------------------- */
/* PROVIDER: My schedule (weekly availability + blocked dates)        */
/* ---------------------------------------------------------------- */
async function renderProviderScheduleView() {
  const root = mainRoot();
  root.innerHTML = "";
  root.append(el("h2", {}, "My schedule"), el("p", { class: "lede" }, "Set your recurring weekly hours and block off one-off dates."));

  const grid = el("div", { class: "grid-2" });
  root.append(grid);

  const hoursCard = el("div", { class: "card" }, [el("h3", {}, "Weekly hours")]);
  const daysWrap = el("div", { id: "days-wrap" });
  hoursCard.append(daysWrap);

  const addForm = el("form", { class: "inline-form", id: "add-window-form", style: "margin-top:1rem;" }, [
    el("div", { class: "field" }, [el("label", {}, "Day"),
      (() => {
        const sel = el("select", { id: "win-day" });
        DAY_NAMES.forEach((d, i) => sel.append(el("option", { value: i }, d)));
        return sel;
      })()]),
    el("div", { class: "field" }, [el("label", {}, "Start"), el("input", { type: "time", id: "win-start", value: "09:00" })]),
    el("div", { class: "field" }, [el("label", {}, "End"), el("input", { type: "time", id: "win-end", value: "17:00" })]),
    el("div", { class: "field" }, [el("label", {}, "Slot (min)"), el("input", { type: "number", id: "win-slot", value: "30", min: "5", step: "5" })]),
    el("button", { class: "btn btn-primary", type: "submit" }, "Add window"),
  ]);
  hoursCard.append(addForm);
  grid.append(hoursCard);

  const blockCard = el("div", { class: "card" }, [el("h3", {}, "Blocked dates")]);
  const blocksWrap = el("div", { id: "blocks-wrap" });
  blockCard.append(blocksWrap);
  const blockForm = el("form", { class: "inline-form", id: "add-block-form", style: "margin-top:1rem;" }, [
    el("div", { class: "field" }, [el("label", {}, "Date"), el("input", { type: "date", id: "block-date", min: todayISO() })]),
    el("div", { class: "field" }, [el("label", {}, "Reason (optional)"), el("input", { type: "text", id: "block-reason", placeholder: "e.g. Conference" })]),
    el("button", { class: "btn btn-primary", type: "submit" }, "Block whole day"),
  ]);
  blockCard.append(blockForm);
  grid.append(blockCard);

  async function refresh() {
    const { windows, blocks } = await Api.get("/api/availability/mine");

    daysWrap.innerHTML = "";
    DAY_NAMES.forEach((name, idx) => {
      const dayWindows = windows.filter((w) => w.dayOfWeek === idx);
      const row = el("div", { class: "day-row" }, [
        el("div", { class: "day-name" }, name),
        el("div", { class: "windows" }, dayWindows.length ? dayWindows.map((w) => {
          const chip = el("span", { class: "window-chip" }, [
            `${w.startTime}–${w.endTime} · ${w.slotMinutes}m`,
          ]);
          const del = el("button", { title: "Remove" }, "×");
          del.onclick = async () => {
            try { await Api.del(`/api/availability/mine/${w.id}`); refresh(); }
            catch (err) { toast(err.message, "error"); }
          };
          chip.append(del);
          return chip;
        }) : [el("span", { style: "color:var(--muted); font-size:0.82rem;" }, "No hours set")]),
        el("span", {}, ""),
      ]);
      daysWrap.append(row);
    });

    blocksWrap.innerHTML = "";
    if (!blocks.length) {
      blocksWrap.append(el("div", { style: "color:var(--muted); font-size:0.85rem;" }, "No blocked dates."));
    } else {
      blocks.forEach((b) => {
        const row = el("div", { class: "day-row" }, [
          el("div", { class: "day-name mono" }, b.date),
          el("div", {}, b.reason || "Time off"),
          (() => {
            const btn = el("button", { class: "btn btn-danger" }, "Remove");
            btn.onclick = async () => {
              try { await Api.del(`/api/availability/mine/block/${b.id}`); refresh(); }
              catch (err) { toast(err.message, "error"); }
            };
            return btn;
          })(),
        ]);
        blocksWrap.append(row);
      });
    }
  }

  addForm.onsubmit = async (e) => {
    e.preventDefault();
    try {
      await Api.post("/api/availability/mine", {
        day_of_week: Number($("#win-day").value),
        start_time: $("#win-start").value,
        end_time: $("#win-end").value,
        slot_minutes: Number($("#win-slot").value),
      });
      toast("Availability window added.", "success");
      refresh();
    } catch (err) { toast(err.message, "error"); }
  };

  blockForm.onsubmit = async (e) => {
    e.preventDefault();
    const date = $("#block-date").value;
    if (!date) { toast("Pick a date to block.", "error"); return; }
    try {
      await Api.post("/api/availability/mine/block", { date, reason: $("#block-reason").value.trim() });
      toast("Date blocked.", "success");
      refresh();
    } catch (err) { toast(err.message, "error"); }
  };

  refresh();
}

/* ---------------------------------------------------------------- */
/* PROVIDER: Appointments                                             */
/* ---------------------------------------------------------------- */
async function renderProviderAppointmentsView() {
  const root = mainRoot();
  root.innerHTML = "";
  root.append(el("h2", {}, "Appointments"), el("p", { class: "lede" }, "Everything on your books."));
  const card = el("div", { class: "card" });
  root.append(card);

  try {
    const { appointments } = await Api.get("/api/appointments/mine");
    if (!appointments.length) {
      card.append(el("div", { class: "empty-state" }, [el("div", { class: "glyph" }, "📭"), "No appointments booked yet."]));
      return;
    }
    const table = el("table", {}, [
      el("thead", {}, el("tr", {}, [el("th", {}, "Client"), el("th", {}, "Date"), el("th", {}, "Time"), el("th", {}, "Status"), el("th", {}, "")])),
    ]);
    const tbody = el("tbody");
    appointments.forEach((a) => {
      const actions = el("div", { style: "display:flex; gap:0.4rem;" });
      if (a.status === "confirmed") {
        const complete = el("button", { class: "btn btn-teal" }, "Complete");
        complete.onclick = async () => { try { await Api.post(`/api/appointments/${a.id}/complete`); renderProviderAppointmentsView(); } catch (err) { toast(err.message, "error"); } };
        const cancel = el("button", { class: "btn btn-danger" }, "Cancel");
        cancel.onclick = async () => { try { await Api.post(`/api/appointments/${a.id}/cancel`); renderProviderAppointmentsView(); } catch (err) { toast(err.message, "error"); } };
        actions.append(complete, cancel);
      }
      tbody.append(el("tr", {}, [
        el("td", {}, a.clientName),
        el("td", { class: "mono" }, a.date),
        el("td", { class: "mono" }, `${a.startTime}–${a.endTime}`),
        el("td", {}, el("span", { class: `status-pill ${a.status}` }, a.status)),
        el("td", {}, actions),
      ]));
    });
    table.append(tbody);
    card.append(table);
  } catch (err) {
    card.append(el("div", { class: "form-msg error" }, err.message));
  }
}


/* ---------------------------------------------------------------- */
/* PROVIDER: What I offer                                            */
/* ---------------------------------------------------------------- */
function money(cents, currency) {
  if (!cents) return "Free";
  const symbol = { CAD: "$", USD: "$", GBP: "£", EUR: "€" }[currency] || "";
  const whole = Math.floor(cents / 100), rem = cents % 100;
  return `${symbol}${rem ? `${whole}.${String(rem).padStart(2, "0")}` : whole}`;
}

async function renderOfferingsView() {
  const root = mainRoot();
  root.innerHTML = "";
  root.append(
    el("h2", {}, "What I offer"),
    el("p", { class: "lede" }, "Sessions people can book, and what they cost."),
  );
  const card = el("div", { class: "card" });
  root.append(card);

  try {
    const { offerings } = await Api.get("/api/offerings/mine");
    if (!offerings.length) {
      card.append(el("div", { class: "empty-state" }, [
        el("div", { class: "glyph" }, "☕"),
        "Nothing listed yet. Add one below, or run seed_luke.py.",
      ]));
    } else {
      const table = el("table", {}, [
        el("thead", {}, el("tr", {}, [
          el("th", {}, "Session"), el("th", {}, "Category"), el("th", {}, "Length"),
          el("th", {}, "Price"), el("th", {}, "Status"), el("th", {}, ""),
        ])),
      ]);
      const tbody = el("tbody");
      offerings.forEach((o) => {
        /* The price is edited in place rather than behind a form. It is the
         * field that actually gets changed, and a dialog for one number is
         * more ceremony than the change deserves. */
        const price = el("input", {
          type: "number", min: "0", step: "1",
          value: String(o.priceCents / 100), style: "width:5.5rem;",
        });
        const save = el("button", { class: "btn btn-teal" }, "Save");
        save.onclick = async () => {
          const dollars = Number(price.value);
          if (!Number.isFinite(dollars) || dollars < 0) {
            return toast("Price must be zero or more.", "error");
          }
          try {
            await Api.patch(`/api/offerings/mine/${o.id}`,
                            { priceCents: Math.round(dollars * 100) });
            toast(`${o.title} is now ${money(Math.round(dollars * 100), o.currency)}.`, "success");
            renderOfferingsView();
          } catch (err) { toast(err.message, "error"); }
        };

        const toggle = el("button",
          { class: o.isActive ? "btn btn-danger" : "btn btn-teal" },
          o.isActive ? "Hide" : "Show");
        toggle.onclick = async () => {
          try {
            if (o.isActive) await Api.del(`/api/offerings/mine/${o.id}`);
            else await Api.patch(`/api/offerings/mine/${o.id}`, { isActive: 1 });
            renderOfferingsView();
          } catch (err) { toast(err.message, "error"); }
        };

        tbody.append(el("tr", { style: o.isActive ? "" : "opacity:.5;" }, [
          el("td", {}, [
            el("div", {}, o.title),
            o.summary ? el("div", { class: "muted", style: "font-size:.78rem;" }, o.summary) : "",
          ]),
          el("td", {}, o.category || "—"),
          el("td", { class: "mono" }, `${o.durationMin} min`),
          el("td", {}, el("div", { style: "display:flex; gap:.4rem; align-items:center;" },
                            [price, save])),
          el("td", {}, el("span",
            { class: `status-pill ${o.isActive ? "confirmed" : "cancelled"}` },
            o.isActive ? "live" : "hidden")),
          el("td", {}, toggle),
        ]));
      });
      table.append(tbody);
      card.append(table);
    }
  } catch (err) {
    card.append(el("div", { class: "form-msg error" }, err.message));
  }

  const add = el("div", { class: "card" });
  add.append(el("h3", {}, "Add a session"));
  const title = el("input", { type: "text", placeholder: "Technical mock interview" });
  const cat = el("select", {});
  ["Software engineering", "Computer science", "Web development", "Careers", "Other"]
    .forEach((c) => cat.append(el("option", { value: c }, c)));
  const dur = el("select", {});
  [15, 30, 45, 60, 90].forEach((d) => dur.append(el("option", { value: String(d) }, `${d} min`)));
  const newPrice = el("input", { type: "number", min: "0", step: "1", value: "0", style: "width:6rem;" });
  const summary = el("input", { type: "text", placeholder: "One line people see in the list" });
  const submit = el("button", { class: "btn btn-primary" }, "Add");
  submit.onclick = async () => {
    if (!title.value.trim()) return toast("Give it a title.", "error");
    try {
      await Api.post("/api/offerings/mine", {
        title: title.value, category: cat.value, summary: summary.value,
        durationMin: Number(dur.value),
        priceCents: Math.round(Number(newPrice.value) * 100),
      });
      toast("Added.", "success");
      renderOfferingsView();
    } catch (err) { toast(err.message, "error"); }
  };
  add.append(
    el("div", { style: "display:flex; gap:.6rem; flex-wrap:wrap; align-items:flex-end;" },
       [title, cat, dur, newPrice, submit]),
    el("div", { style: "margin-top:.6rem;" }, summary),
    el("p", { class: "muted", style: "font-size:.78rem; margin-top:.6rem;" },
      "A session must start and end on a slot boundary, so your slot size decides "
      + "which lengths are bookable at all — a 45-minute session needs a 15-minute grid."),
  );
  root.append(add);
}

/* ---------------------------------------------------------------- */
/* PROVIDER: Coffee chats                                            */
/* ---------------------------------------------------------------- */
const INVITE_PILL = {
  sent: "pending", viewed: "pending", booked: "confirmed",
  declined: "cancelled", expired: "cancelled", revoked: "cancelled",
};

async function renderCoffeeView() {
  const root = mainRoot();
  root.innerHTML = "";
  root.append(
    el("h2", {}, "Coffee chats"),
    el("p", { class: "lede" }, "Send someone a link. They pick a time without signing up."),
  );

  const send = el("div", { class: "card" });
  send.append(el("h3", {}, "Send an invite"));
  const email = el("input", { type: "email", placeholder: "them@example.com" });
  const name = el("input", { type: "text", placeholder: "Their name (optional)" });
  const offering = el("select", {});
  offering.append(el("option", { value: "" }, "General chat — 30 min"));
  const note = el("input", { type: "text", placeholder: "A line of context (optional)" });
  const go = el("button", { class: "btn btn-primary" }, "Send invite");

  try {
    const { offerings } = await Api.get("/api/offerings/mine");
    offerings.filter((o) => o.isActive).forEach((o) => {
      offering.append(el("option", { value: String(o.id) },
        `${o.title} — ${money(o.priceCents, o.currency)}, ${o.durationMin} min`));
    });
  } catch (_e) { /* the catalogue is optional; a general chat still works */ }

  go.onclick = async () => {
    if (!email.value.trim()) return toast("Who is it for?", "error");
    const body = { email: email.value, name: name.value, message: note.value };
    if (offering.value) body.offeringId = Number(offering.value);
    go.disabled = true;
    try {
      await Api.post("/api/coffee/invites", body);
      toast(`Invite sent to ${email.value}.`, "success");
      renderCoffeeView();
    } catch (err) { toast(err.message, "error"); go.disabled = false; }
  };
  send.append(
    el("div", { style: "display:flex; gap:.6rem; flex-wrap:wrap; align-items:flex-end;" },
       [email, name, offering, go]),
    el("div", { style: "margin-top:.6rem;" }, note),
  );
  root.append(send);

  const card = el("div", { class: "card" });
  root.append(card);
  try {
    const { invites, stats } = await Api.get("/api/coffee/invites");

    if (stats && stats.total) {
      const rate = stats.bookedRate == null ? "—" : `${Math.round(stats.bookedRate * 100)}%`;
      card.append(el("p", { class: "muted", style: "margin-top:0;" },
        `${stats.total} invite${stats.total === 1 ? "" : "s"} sent · ${rate} booked`));
    }
    if (!invites.length) {
      card.append(el("div", { class: "empty-state" }, [
        el("div", { class: "glyph" }, "✉️"), "No invites yet.",
      ]));
      return;
    }

    const table = el("table", {}, [
      el("thead", {}, el("tr", {}, [
        el("th", {}, "Guest"), el("th", {}, "About"), el("th", {}, "Sent"),
        el("th", {}, "Status"), el("th", {}, ""),
      ])),
    ]);
    const tbody = el("tbody");
    invites.forEach((i) => {
      const actions = el("div", { style: "display:flex; gap:.4rem;" });
      const open = i.status === "sent" || i.status === "viewed";
      if (open) {
        /* Copying the link matters as much as emailing it: plenty of people
         * would rather paste it into a message they are already writing. */
        const copy = el("button", { class: "btn btn-ghost" }, "Copy link");
        copy.onclick = async () => {
          const url = `${location.origin}/coffee/${i.token}`;
          try {
            await navigator.clipboard.writeText(url);
            toast("Link copied.", "success");
          } catch (_e) { prompt("Copy this link:", url); }
        };
        const nudge = el("button", { class: "btn btn-teal" }, "Nudge");
        nudge.onclick = async () => {
          try {
            await Api.post(`/api/coffee/invites/${i.id}/nudge`);
            toast("Follow-up sent.", "success");
            renderCoffeeView();
          } catch (err) { toast(err.message, "error"); }
        };
        const revoke = el("button", { class: "btn btn-danger" }, "Revoke");
        revoke.onclick = async () => {
          try { await Api.del(`/api/coffee/invites/${i.id}`); renderCoffeeView(); }
          catch (err) { toast(err.message, "error"); }
        };
        actions.append(copy, nudge, revoke);
      }
      const nudged = i.nudgeCount ? ` · nudged ${i.nudgeCount}×` : "";
      tbody.append(el("tr", {}, [
        el("td", {}, [
          el("div", {}, i.guestName || i.guestEmail),
          i.guestName ? el("div", { class: "muted", style: "font-size:.78rem;" }, i.guestEmail) : "",
        ]),
        el("td", {}, i.topic || "Coffee chat"),
        el("td", { class: "mono" }, (i.createdAt || "").slice(0, 10)),
        el("td", {}, [
          el("span", { class: `status-pill ${INVITE_PILL[i.status] || "pending"}` }, i.status),
          el("span", { class: "muted", style: "font-size:.72rem;" }, nudged),
        ]),
        el("td", {}, actions),
      ]));
    });
    table.append(tbody);
    card.append(table);
  } catch (err) {
    card.append(el("div", { class: "form-msg error" }, err.message));
  }
}

/* ---------------------------------------------------------------- */
/* ADMIN: Users                                                       */
/* ---------------------------------------------------------------- */
async function renderAdminUsersView() {
  const root = mainRoot();
  root.innerHTML = "";
  root.append(el("h2", {}, "Users"), el("p", { class: "lede" }, "Everyone with an account on the platform."));
  const card = el("div", { class: "card" });
  root.append(card);

  try {
    const { users } = await Api.get("/api/admin/users");
    const table = el("table", {}, [
      el("thead", {}, el("tr", {}, [el("th", {}, "Name"), el("th", {}, "Email"), el("th", {}, "Role"), el("th", {}, "Status"), el("th", {}, "")])),
    ]);
    const tbody = el("tbody");
    users.forEach((u) => {
      const toggleBtn = el("button", { class: u.isActive ? "btn btn-danger" : "btn btn-teal" }, u.isActive ? "Deactivate" : "Activate");
      toggleBtn.onclick = async () => {
        try { await Api.patch(`/api/admin/users/${u.id}`, { is_active: !u.isActive }); renderAdminUsersView(); }
        catch (err) { toast(err.message, "error"); }
      };
      tbody.append(el("tr", {}, [
        el("td", {}, u.name),
        el("td", { class: "mono" }, u.email),
        el("td", {}, el("span", { class: `role-badge ${u.role}` }, u.role)),
        el("td", {}, u.isActive ? "Active" : "Deactivated"),
        el("td", {}, toggleBtn),
      ]));
    });
    table.append(tbody);
    card.append(table);
  } catch (err) {
    card.append(el("div", { class: "form-msg error" }, err.message));
  }
}

/* ---------------------------------------------------------------- */
/* ADMIN: All appointments                                            */
/* ---------------------------------------------------------------- */
async function renderAdminAppointmentsView() {
  const root = mainRoot();
  root.innerHTML = "";
  root.append(el("h2", {}, "All appointments"), el("p", { class: "lede" }, "Platform-wide booking activity."));
  const card = el("div", { class: "card" });
  root.append(card);

  try {
    const { appointments } = await Api.get("/api/appointments/mine");
    if (!appointments.length) {
      card.append(el("div", { class: "empty-state" }, [el("div", { class: "glyph" }, "📭"), "No appointments booked yet."]));
      return;
    }
    const table = el("table", {}, [
      el("thead", {}, el("tr", {}, [el("th", {}, "Client"), el("th", {}, "Provider"), el("th", {}, "Date"), el("th", {}, "Time"), el("th", {}, "Status")])),
    ]);
    const tbody = el("tbody");
    appointments.forEach((a) => {
      tbody.append(el("tr", {}, [
        el("td", {}, a.clientName),
        el("td", {}, a.providerName),
        el("td", { class: "mono" }, a.date),
        el("td", { class: "mono" }, `${a.startTime}–${a.endTime}`),
        el("td", {}, el("span", { class: `status-pill ${a.status}` }, a.status)),
      ]));
    });
    table.append(tbody);
    card.append(table);
  } catch (err) {
    card.append(el("div", { class: "form-msg error" }, err.message));
  }
}

/* ---------------------------------------------------------------- */
/* ADMIN: Email log                                                   */
/* ---------------------------------------------------------------- */
const EMAIL_KIND_LABELS = {
  welcome: "Welcome",
  booked_client: "Booking confirmed → client",
  booked_provider: "New booking → provider",
  cancelled_client: "Cancelled → client",
  cancelled_provider: "Cancelled → provider",
  completed_client: "Completed → client",
  reminder_client: "Reminder → client",
  reminder_provider: "Reminder → provider",
  test: "Test email",
};

async function renderAdminEmailsView() {
  const root = mainRoot();
  root.innerHTML = "";
  root.append(
    el("h2", {}, "Email log"),
    el("p", { class: "lede" }, "Every message the platform has sent automatically.")
  );

  const toolbar = el("div", { class: "card" }, [el("h3", {}, "Delivery")]);
  const status = el("div", { class: "mail-status" });
  const actions = el("div", { class: "inline-form", style: "margin-top:1rem;" });
  toolbar.append(status, actions);
  root.append(toolbar);

  const card = el("div", { class: "card" });
  root.append(card);

  const testField = el("div", { class: "field" }, [
    el("label", { for: "test-email-to" }, "Send a test email to"),
    el("input", { type: "email", id: "test-email-to", placeholder: "you@example.com" }),
  ]);
  const testBtn = el("button", { class: "btn btn-primary" }, "Send test");
  testBtn.onclick = async () => {
    testBtn.disabled = true;
    try {
      const body = {};
      const to = $("#test-email-to").value.trim();
      if (to) body.to = to;
      const res = await Api.post("/api/admin/emails/test", body);
      toast(`Test email queued for ${res.to}.`, "success");
      setTimeout(load, 600);
    } catch (err) { toast(err.message, "error"); }
    finally { testBtn.disabled = false; }
  };

  const remindBtn = el("button", { class: "btn btn-teal" }, "Run reminders now");
  remindBtn.onclick = async () => {
    remindBtn.disabled = true;
    try {
      const { queued } = await Api.post("/api/admin/emails/run-reminders", {});
      toast(queued ? `${queued} reminder email(s) queued.` : "No reminders were due.", queued ? "success" : "info");
      setTimeout(load, 600);
    } catch (err) { toast(err.message, "error"); }
    finally { remindBtn.disabled = false; }
  };

  const refreshBtn = el("button", { class: "btn btn-ghost" }, "Refresh");
  refreshBtn.onclick = () => load();

  actions.append(testField, testBtn, remindBtn, refreshBtn);

  async function load() {
    card.innerHTML = "";
    let data;
    try {
      data = await Api.get("/api/admin/emails");
    } catch (err) {
      card.append(el("div", { class: "form-msg error" }, err.message));
      return;
    }

    status.innerHTML = "";
    status.append(
      el("span", { class: `status-pill ${data.enabled ? "sent" : "failed"}` }, data.enabled ? "Mail on" : "Mail off"),
      el("span", { class: "mono" }, data.transport === "smtp" ? "SMTP server" : "Console (no SMTP_HOST set)"),
      el("span", { class: "mono" }, `Reminders ${Number(data.reminderHoursBefore).toFixed(0)}h ahead`)
    );

    if (!data.emails.length) {
      card.append(el("div", { class: "empty-state" }, [
        el("div", { class: "glyph" }, "✉"),
        "Nothing sent yet. Book an appointment, or send yourself a test.",
      ]));
      return;
    }

    const table = el("table", {}, [
      el("thead", {}, el("tr", {}, [
        el("th", {}, "Sent"), el("th", {}, "Type"), el("th", {}, "To"),
        el("th", {}, "Subject"), el("th", {}, "Status"),
      ])),
    ]);
    const tbody = el("tbody");
    data.emails.forEach((m) => {
      const statusCell = el("td", {}, [
        el("span", { class: `status-pill ${m.status}` }, m.status),
      ]);
      if (m.error) statusCell.append(el("div", { class: "mail-error", title: m.error }, m.error));
      tbody.append(el("tr", {}, [
        el("td", { class: "mono" }, String(m.sentAt || m.createdAt || "").slice(0, 16)),
        el("td", {}, EMAIL_KIND_LABELS[m.kind] || m.kind),
        el("td", { class: "mono" }, m.recipient),
        el("td", {}, m.subject),
        statusCell,
      ]));
    });
    table.append(tbody);
    card.append(table);
  }

  load();
}

/* ---------------------------------------------------------------- */
/* Boot                                                                */
/* ---------------------------------------------------------------- */
async function bootApp() {
  const token = Api.getToken();
  if (!token) { renderAuthScreen(); return; }
  try {
    const { user } = await Api.get("/api/auth/me");
    Api.setSession(token, user);
    renderAppScreen(user);
  } catch (_err) {
    Api.clearSession();
    renderAuthScreen();
  }
}

document.addEventListener("DOMContentLoaded", bootApp);

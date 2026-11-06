// The presentation at /tour: arrow keys move between slides, and the race button sends 50
// real bookings for one slot from this browser at once. Kept in its own file because the
// Content-Security-Policy forbids inline scripts.

document.addEventListener("keydown", (event) => {
  if (event.target.closest("input, textarea, select")) return;
  const rel = { ArrowRight: "next", ArrowLeft: "prev" }[event.key];
  const link = rel && document.querySelector(`a[rel="${rel}"]`);
  if (link) window.location.href = link.href;
});

const RACERS = 50;

async function runRace(button, result) {
  button.disabled = true;
  result.textContent = "Finding a free slot…";
  const slotResponse = await fetch(button.dataset.slotUrl, { headers: { Accept: "application/json" } });
  if (!slotResponse.ok) {
    result.textContent = "Couldn't find a free slot. Try signing in again.";
    button.disabled = false;
    return;
  }
  const slot = await slotResponse.json();
  const csrf = button.dataset.csrf;
  result.textContent = `Sending ${RACERS} bookings for ${slot.room}, ${slot.label}…`;

  const book = () =>
    fetch(slot.bookings_url, {
      method: "POST",
      headers: { Accept: "application/json", "X-CSRF-Token": csrf },
      body: new URLSearchParams({
        room_id: slot.room_id,
        start: slot.start,
        duration_minutes: "30",
        title: "Race demo",
        csrf,
      }),
    });
  const started = performance.now();
  const responses = await Promise.all(Array.from({ length: RACERS }, book));
  const elapsed = Math.round(performance.now() - started);

  const created = responses.filter((r) => r.status === 201);
  const conflicts = responses.filter((r) => r.status === 409).length;
  const other = RACERS - created.length - conflicts;
  const sample = conflicts ? (await responses.find((r) => r.status === 409).json()).error : "";

  // Leave no trace: cancel the winning booking.
  for (const winner of created) {
    const { id } = await winner.json();
    await fetch(`${slot.bookings_url}/${id}/cancel`, {
      method: "POST",
      headers: { "X-CSRF-Token": csrf },
      body: new URLSearchParams({ csrf }),
      redirect: "manual",
    });
  }

  result.replaceChildren();
  const add = (tag, text, className) => {
    const element = document.createElement(tag);
    element.textContent = text;
    if (className) element.className = className;
    result.append(element);
  };
  add("p", `${created.length} created`, created.length === 1 ? "race-win" : "race-bad");
  add("p", `${conflicts} turned away by the database`, "race-lose");
  if (other) add("p", `${other} other responses`, "race-bad");
  add("p", `All ${RACERS} sent in ${elapsed} ms. Each refusal said: "${sample}"`, "muted");
  button.disabled = false;
}

const raceButton = document.getElementById("race-button");
if (raceButton) {
  raceButton.addEventListener("click", () => runRace(raceButton, document.getElementById("race-result")));
}

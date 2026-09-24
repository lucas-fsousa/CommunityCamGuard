# Dashboard settings

The **Settings / Configurações** tab manages two server preferences in English or
Portuguese. It is not a camera-control panel or an editor for the whole `.env`.
See the [rollout checkpoint](../internal/runtime-settings.md) for deployment status.

## Access and editing

Sign in with the server's primary dashboard key. Old sessions can still monitor
cameras, but cannot administer settings: sign out and sign in again. The backend
enforces this independently of the UI. Temporary access keys are not implemented.

The centered panel uses compact numeric fields beside each setting's title, with
explanations below and separate save/reload actions. It adapts to narrow screens.

Each field shows its current resolved value, including when disabled. **Use server default**
means no database override, so the number input is disabled. Uncheck it to enter a
value and click **Save changes**. Check it again and save to restore the environment
baseline. Nothing is written until Save; unchanged/busy forms cannot be submitted.

| Setting | Range and meaning | When it takes effect |
| --- | --- | --- |
| HD camera limit in Auto mode | 0–64. Zero uses the substream in Auto. Larger limits may increase CPU use; explicit HD/SD choices are unchanged. | Returning to live view in this tab; reload other tabs. |
| Playback cache limit | 0–65536 MiB. **Zero means unlimited.** Only derived playback copies, never original recordings. | Next cache policy check; Save does not trigger immediate eviction. |

Entering Settings suspends this tab's live players, like Cameras/Recordings.
Recording and other viewers are not deliberately restarted. Returning to live view
reconnects this tab using the saved Auto budget.

## Conflicts and uncertain saves

If another session saved first, your stale revision is rejected. **Discard edits
and reload** fetches the saved state; review it before re-entering changes. There
is no automatic retry or forced overwrite. Reload is also required if a save's
result cannot be confirmed: the server may already have committed it. Leaving the
view/signing out cancels browser requests, not a completed server transaction.

Retention, credentials, ports, camera controls and temporary-key management are not
part of this screen. Environment files are never rewritten; DB overrides apply only
to these two fields. See [API](api.md), [recordings](recordings.md) and [roadmap](../../ROADMAP.md).

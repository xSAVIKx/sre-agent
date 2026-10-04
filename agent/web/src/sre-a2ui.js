/**
 * The chat UI's A2UI renderer: @a2ui/lit (protocol v0.9) plus the SRE catalog.
 *
 * The SRE agent describes each result as an A2UI surface - a list of messages
 * (createSurface, updateComponents, updateDataModel) naming catalog components.
 * This module renders them. It is bundled into agent/src/agent/static/sre-a2ui.js
 * (`npm run build`), so the chat page needs no build step and works offline.
 *
 * The SRE catalog is A2UI's basic catalog plus two components of our own,
 * mirroring sre_agent/src/sre_agent/a2ui_surfaces.py:
 *   - SeverityBadge {level, contribution}: SEV1/2/3 and the bottleneck's share.
 *   - Download {label, filename, content}: saves the content as a file.
 */
import {css, html, LitElement, nothing} from 'lit';
import {ContextProvider} from '@lit/context';
import {z} from 'zod';
import {Catalog, CommonSchemas, MessageProcessor} from '@a2ui/web_core/v0_9';
import {BASIC_FUNCTIONS, Context} from '@a2ui/web_core/v0_9/basic_catalog';
import {A2uiLitElement, basicCatalog} from '@a2ui/lit/v0_9';
import {renderMarkdown} from '@a2ui/markdown-it';

export const SRE_CATALOG_ID = 'https://github.com/xSAVIKx/sre-agent/a2ui/catalogs/sre/v1/catalog.json';

// --- SeverityBadge ----------------------------------------------------------

const SeverityBadgeApi = {
  name: 'SeverityBadge',
  schema: z
    .object({
      level: CommonSchemas.DynamicString.describe('SEV1 (worst), SEV2 or SEV3.'),
      contribution: CommonSchemas.DynamicNumber.describe("The bottleneck span's share of the request, in percent."),
    })
    .strict(),
};

class SreSeverityBadge extends A2uiLitElement {
  api = SeverityBadgeApi;

  // A2uiLitElement renders in light DOM and adopts these styles into the surface's root.
  static styles = css`
    :host {
      display: block;
    }
    .badge {
      display: inline-block;
      padding: 4px 12px;
      border-radius: 999px;
      font-weight: 600;
      font-size: 0.85rem;
      border: 1px solid currentColor;
    }
    .sev1 {
      color: var(--sre-sev1, #f87171);
      background: rgba(239, 68, 68, 0.15);
    }
    .sev2 {
      color: var(--sre-sev2, #fbbf24);
      background: rgba(245, 158, 11, 0.15);
    }
    .sev3 {
      color: var(--sre-sev3, #60a5fa);
      background: rgba(59, 130, 246, 0.15);
    }
  `;

  render() {
    const props = this.controller?.props;
    if (!props) return nothing;
    const level = String(props.level ?? '');
    const share = Number(props.contribution ?? 0).toFixed(1);
    return html`<span class="badge ${level.toLowerCase()}">${level} · bottleneck owns ${share}% of the request</span>`;
  }
}
customElements.define('sre-severity-badge', SreSeverityBadge);

// --- Download -----------------------------------------------------------------

const DownloadApi = {
  name: 'Download',
  schema: z
    .object({
      label: CommonSchemas.DynamicString.describe('The button label.'),
      filename: CommonSchemas.DynamicString.describe('The file name to save as.'),
      content: CommonSchemas.DynamicString.describe('The file content.'),
    })
    .strict(),
};

class SreDownload extends A2uiLitElement {
  api = DownloadApi;

  static styles = css`
    button {
      font: inherit;
      padding: 8px 16px;
      border-radius: var(--a2ui-border-radius, 0.25rem);
      border: 1px solid var(--a2ui-color-border, #444);
      background: var(--a2ui-color-secondary, #333);
      color: var(--a2ui-color-on-secondary, #eee);
      cursor: pointer;
    }
    button:hover {
      background: var(--a2ui-color-secondary-hover, #444);
    }
  `;

  save() {
    const {filename, content} = this.controller.props;
    const url = URL.createObjectURL(new Blob([String(content ?? '')], {type: 'text/markdown'}));
    const link = Object.assign(document.createElement('a'), {href: url, download: String(filename || 'report.md')});
    link.click();
    URL.revokeObjectURL(url);
  }

  render() {
    const props = this.controller?.props;
    if (!props) return nothing;
    // The file name tells several "Download" buttons apart for screen readers.
    return html`<button aria-label="${props.label}: ${props.filename}" @click=${() => this.save()}>
      <span aria-hidden="true">📥</span> ${props.label}
    </button>`;
  }
}
customElements.define('sre-download', SreDownload);

/** The SRE catalog: everything in the basic catalog, plus SeverityBadge and Download. */
export const sreCatalog = new Catalog(
  SRE_CATALOG_ID,
  '0.9',
  [
    ...basicCatalog.components.values(),
    {...SeverityBadgeApi, tagName: 'sre-severity-badge'},
    {...DownloadApi, tagName: 'sre-download'},
  ],
  BASIC_FUNCTIONS,
);

// --- Rendering ------------------------------------------------------------------

// Markdown details the basic catalog leaves to the host (long log lines, report tables),
// adopted into the surface's shadow root, which page CSS cannot reach.
const SURFACE_SHEET = new CSSStyleSheet();
SURFACE_SHEET.replaceSync(`
  pre { white-space: pre-wrap; word-break: break-word; overflow-x: auto; }
  code { overflow-wrap: anywhere; }
  table { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; margin: 8px 0; font-size: 0.9em; }
  th, td { border: 1px solid var(--a2ui-color-border, #444); padding: 4px 8px; text-align: left; }
  button:focus-visible { outline: 2px solid var(--sre-focus, #a5b4fc); outline-offset: 2px; }
`);

/**
 * Adds the ARIA roles that @a2ui/lit 0.12 does not set: its Tabs are plain buttons and
 * its List items plain divs. With the roles, a screen reader announces the selected tab,
 * and each row's buttons ("Diagnose") in the context of their list item.
 */
function addAriaRoles(root) {
  for (const bar of root.querySelectorAll('.a2ui-tab-bar')) {
    bar.setAttribute('role', 'tablist');
    for (const tab of bar.querySelectorAll('.a2ui-tab-button')) {
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-selected', String(tab.classList.contains('active')));
    }
  }
  for (const list of root.querySelectorAll('.a2ui-list')) {
    list.setAttribute('role', 'list');
    for (const item of list.children) item.setAttribute('role', 'listitem');
  }
}

/** Hosts one surface and provides the Markdown renderer the basic Text component consumes. */
class SreSurfaceHost extends LitElement {
  static properties = {surface: {attribute: false}};

  constructor() {
    super();
    // Sanitizing renderer (markdown-it + DOMPurify): surfaces come from another agent.
    new ContextProvider(this, {context: Context.markdown, initialValue: renderMarkdown});
  }

  createRenderRoot() {
    return this; // light DOM: the page's .sre-surface CSS variables apply
  }

  async updated() {
    const element = this.querySelector('a2ui-surface');
    await element?.updateComplete;
    const root = element?.shadowRoot;
    if (!root || root.adoptedStyleSheets.includes(SURFACE_SHEET)) return;
    root.adoptedStyleSheets = [...root.adoptedStyleSheets, SURFACE_SHEET];
    // Components render after the surface: keep the roles current as they (and tabs) change.
    addAriaRoles(root);
    new MutationObserver(() => addAriaRoles(root)).observe(root, {
      subtree: true,
      childList: true,
      attributeFilter: ['class'],
    });
  }

  render() {
    return this.surface ? html`<a2ui-surface .surface=${this.surface}></a2ui-surface>` : nothing;
  }
}
customElements.define('sre-surface-host', SreSurfaceHost);

/**
 * Renders one A2UI surface into `container`.
 *
 * @param {HTMLElement} container Where the surface goes.
 * @param {object[]} messages The surface's A2UI messages, as the agent sent them.
 * @param {(action: {name: string, context: object, surfaceId: string}) => void} onAction
 *     Called when the user triggers an action (e.g. a button) on the surface.
 * @returns {HTMLElement} The rendered host element.
 */
export function renderSurface(container, messages, onAction) {
  const processor = new MessageProcessor([sreCatalog], action => onAction?.(action));
  const host = document.createElement('sre-surface-host');
  processor.onSurfaceCreated(surface => {
    host.surface = surface;
  });
  processor.processMessages(messages);
  container.appendChild(host);
  return host;
}

// The chat page is a classic script: it calls window.SreA2ui.renderSurface().
window.SreA2ui = {renderSurface, SRE_CATALOG_ID};

// Set by the private production host before loading the normal React bundle.
// API access is enforced independently by the explicit server route catalog.
export const RESTRICTED_WEB = (window as Window & { __WP_RESTRICTED_WEB__?: boolean }).__WP_RESTRICTED_WEB__ === true

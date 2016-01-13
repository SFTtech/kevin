export function initialize(/* application */) {
  // application.inject('route', 'foo', 'service:foo');
}

export default {
  name: 'websockets',
  initialize(container, app) {
    app.inject('controller', 'websockets', 'service:websockets');
    // TODO: remove, only keep in controller.
    app.inject('route', 'websockets', 'service:websockets');
  }
};

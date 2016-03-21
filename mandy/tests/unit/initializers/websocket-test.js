import Ember from 'ember';
import WebsocketInitializer from '../../../initializers/websocket';
import { module, test } from 'qunit';

let application;

module('Unit | Initializer | websocket', {
  beforeEach() {
    Ember.run(function() {
      application = Ember.Application.create();
      application.deferReadiness();
    });
  }
});

// Replace this with your real tests.
test('it works', function(assert) {
  WebsocketInitializer.initialize(application);

  // you would normally confirm the results of the initializer here
  assert.ok(true);
});

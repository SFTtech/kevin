import Ember from 'ember';
import config from './config/environment';

const Router = Ember.Router.extend({
  location: config.locationType
});

Router.map(function() {
  this.route("index", { path: "/" });
  this.route("about");
  this.route("projects");
  this.route("invalid-page", { path: "/*wildcard" });
});

export default Router;

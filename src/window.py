"""
Main code for the application window.
"""

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Vte  # noqa: F401
from gi.repository.Vte import Terminal as VteTerminal  # noqa: F401
import serial.tools.list_ports
import time
import threading

from .config import config, Parity, FlowControl, to_enum_str, from_enum_str, enum_to_stringlist
from .common import disallow_nonnumeric, find_in_stringlist, copy_list_to_stringlist
from .serial import SerialHandler

@Gtk.Template(resource_path='/com/github/knuxify/SerialBowl/ui/window.ui')
class SerialBowlWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'SerialBowlWindow'

    sidebar = Gtk.Template.Child()
    terminal = Gtk.Template.Child()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.set_terminal_color_scheme()

        self.serial = SerialHandler()
        self.serial.connect('read_done', self.terminal_read)
        self.serial.bind_property(
            'is-open', self.terminal, 'sensitive',
            GObject.BindingFlags.SYNC_CREATE
        )

        self.ports = Gtk.StringList()
        self.get_available_ports()
        self.port_update_thread = threading.Thread(target=self.port_update_loop, daemon=True)
        self.port_update_thread.start()

    def get_available_ports(self):
        ports = sorted([port[0] for port in serial.tools.list_ports.comports()])
        copy_list_to_stringlist(ports, self.ports)
        try:
            self.sidebar.port_selector.set_selected(
                find_in_stringlist(self.ports, self.serial.port)
            )
        except (TypeError, ValueError):
            pass

        if not self.serial.is_open:
            self.sidebar.open_button.set_sensitive(bool(ports))

    def port_update_loop(self):
        while True:
            GLib.idle_add(self.get_available_ports)
            time.sleep(1)

    # Console handling functions

    @Gtk.Template.Callback()
    def terminal_commit(self, terminal, text, size, *args):
        """Get input from the terminal and send it over serial."""
        self.serial.write_text(text)

    def terminal_read(self, serial, data, *args):
        self.terminal.feed(data.get_data())

    def terminal_write_message(self, text):
        """Writes an info message to the terminal."""
        if not self.terminal.get_text()[0].strip():
            self.terminal.feed(
                bytes(f'\r\033[0;90m--- {text} ---\r\n\033[0m', 'utf-8')
            )
        else:
            self.terminal.feed(
                bytes(f'\r\n\033[0;90m--- {text} ---\r\n\033[0m', 'utf-8')
            )

    def set_terminal_color_scheme(self):
        """Sets up a terminal color scheme from the default colors."""
        style = self.get_style_context()
        bg = style.lookup_color('view_bg_color')[1]
        fg = style.lookup_color('view_fg_color')[1]
        self.terminal.set_color_background(bg)
        self.terminal.set_color_foreground(fg)

@Gtk.Template(resource_path='/com/github/knuxify/SerialBowl/ui/settings-pane.ui')
class SerialBowlSettingsPane(Gtk.Box):
    __gtype_name__ = 'SerialBowlSettingsPane'

    open_button_switcher = Gtk.Template.Child()
    open_button = Gtk.Template.Child()
    close_button = Gtk.Template.Child()

    reconnect_automatically = Gtk.Template.Child()

    port_selector = Gtk.Template.Child()
    baudrate_selector = Gtk.Template.Child()
    custom_baudrate = Gtk.Template.Child()

    data_bits_selector = Gtk.Template.Child()
    parity_selector = Gtk.Template.Child()
    stop_bits_selector = Gtk.Template.Child()
    flow_control_selector = Gtk.Template.Child()

    def __init__(self):
        super().__init__()
        self.reconnect_thread = None
        self._needs_setup = True

        # Only allow numbers to be typed into custom baud rate field
        self.custom_baudrate.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self.custom_baudrate.get_delegate().connect('insert-text', disallow_nonnumeric)

        self.connect('realize', self._setup)

    def _setup(self, *args):
        """
        get_native returns NULL before the window is fully displayed,
        so we need to do setup then.
        """
        if not self._needs_setup:
            return

        self.serial = self.get_native().serial
        self.serial.connect('notify::is-open', self.update_open_button)

        self.ports = self.get_native().ports
        self.port_selector.set_model(self.ports)

        self.update_open_button()

        self.setup_settings_bindings()

        self._needs_setup = False

    def setup_settings_bindings(self):
        config.bind(
            'reconnect-automatically',
            self.reconnect_automatically, 'active',
            flags=Gio.SettingsBindFlags.DEFAULT
        )

        # baud-rate is not included here, as its selector is set up separately.
        # We don't do bindings here since each selector has its own "set from
        # selector" function (and it wouldn't be possible since we need to
        # convert from string to int first anyways).
        params = {
            'port': ('selector', self.port_selector),
            'data-bits': ('selector', self.data_bits_selector),
            'stop-bits': ('selector', self.stop_bits_selector),
        }

        # Serial parameters are directly synced to config:
        for property in ('port', 'baud-rate', 'data-bits', 'stop-bits'):

            if property == 'port':
                # For ports, there is no guarantee that the last used
                # port will be available, so we set the available port
                # if and only if it's actually available; otherwise we
                # get the first item in the model.
                ports = [p.get_string() for p in self.get_native().ports]
                if config['port'] in ports:
                    port = config['port']
                elif ports:
                    port = ports[0]
                    config['port'] = port
                else:
                    port = ''
                    config['port'] = port
                self.serial.port = port
            else:
                self.serial.set_property(property, config[property])

            config.bind(
                property, self.serial, property,
                flags=Gio.SettingsBindFlags.DEFAULT
            )

            if property in params:
                if params[property][0] == 'selector':
                    selector = params[property][1]
                    i = find_in_stringlist(selector.get_model(), str(config[property]))
                    if i is None:
                        i = 0
                    selector.set_selected(i)

        # Enum properties need to be handled separately, else they
        # end up syncing the *strings*, not the *IDs*:
        enums = {
            'parity': (Parity, self.parity_selector),
            'flow-control': (FlowControl, self.flow_control_selector),
        }

        for property in ('parity', 'flow-control'):
            self.serial.set_property(property, config.get_enum(property))

            config.bind(
                property, self, property + '-str',
                flags=Gio.SettingsBindFlags.DEFAULT
            )

            selector = enums[property][1]
            selector.set_model(enum_to_stringlist(enums[property][0]))
            selector.bind_property('selected', self.serial, property,
                GObject.BindingFlags.BIDIRECTIONAL
            )
            selector.set_selected(config.get_enum(property))
            self.serial.connect('notify::' + property, lambda *args: self.notify(property + '-str'))

        # Set up baud rate selector
        baudrate_model = self.baudrate_selector.get_model()
        for i in range(baudrate_model.get_n_items()):
            rate = baudrate_model.get_item(i).get_string()
            try:
                if int(rate) == config['baud-rate']:
                    self.baudrate_selector.set_selected(i)
                    break
            except ValueError:  # custom
                self.custom_baudrate.set_text(str(config['baud-rate']))
                self.baudrate_selector.set_selected(i)

    @GObject.Property(type=str)
    def parity_str(self):
        """Workaround to allow us to sync settings."""
        return to_enum_str(Parity, self.serial.parity)

    @parity_str.setter
    def parity_str(self, value):
        self.serial.parity = from_enum_str(Parity, value)

    @GObject.Property(type=str)
    def flow_control_str(self):
        """Workaround to allow us to sync settings."""
        return to_enum_str(FlowControl, self.serial.flow_control)

    @flow_control_str.setter
    def flow_control_str(self, value):
        self.serial.flow_control = from_enum_str(FlowControl, value)

    def update_open_button(self, *args):
        if self.serial.is_open:
            self.open_button.set_sensitive(False)
            self.close_button.set_sensitive(True)
            self.open_button_switcher.set_visible_child(self.close_button)
        else:
            # Handle lost connection
            if self.serial._connection_lost:
                if config['reconnect-automatically']:
                    self.start_reconnect_thread()
                    return
                else:
                    self.serial._connection_lost = False

            self.open_button.set_sensitive(bool(self.ports.get_n_items()))
            self.close_button.set_sensitive(False)
            self.open_button_switcher.set_visible_child(self.open_button)

    def reconnect_loop(self):
        """Awaits for the currently opened device to come back online."""
        while not self.serial.is_open:
            if self.serial._force_close:
                self.serial._connection_lost = False
                self.reconnect_thread = None
                GLib.idle_add(self.update_open_button)
                return

            if self.serial.port in [p.get_string() for p in self.ports]:
                break

            time.sleep(1.25)
        self.serial._connection_lost = False
        self.reconnect_thread = None
        GLib.idle_add(self.serial.open)

    def start_reconnect_thread(self):
        self.get_native().get_available_ports()
        if self.reconnect_thread:
            return
        self.reconnect_thread = threading.Thread(target=self.reconnect_loop, daemon=True)
        self.reconnect_thread.start()

    @Gtk.Template.Callback()
    def open_serial(self, *args):
        self.open_button.set_sensitive(False)
        self.serial.open()

    @Gtk.Template.Callback()
    def close_serial(self, *args):
        self.serial._force_close = True
        self.close_button.set_sensitive(False)
        self.serial.close()

    @Gtk.Template.Callback()
    def set_port_from_selector(self, selector, *args):
        if self._needs_setup:
            return

        try:
            port = selector.get_selected_item().get_string()
        except AttributeError:
            return
        if port == self.serial.port:
            return
        self.serial.port = port

        _switched_port_text = _("switched port to {port}").format(port=port)
        self.get_native().terminal_write_message(_switched_port_text)

    @Gtk.Template.Callback()
    def set_baudrate_from_selector(self, *args):
        # This is called for both the selector and updates on the custom
        # baudrate, so we ignore the passed selector value.
        try:
            baudrate = int(self.baudrate_selector.get_selected_item().get_string())
            self.custom_baudrate.set_sensitive(False)
        except ValueError:  # selected rate is a string, so Custom
            self.custom_baudrate.set_sensitive(True)
            try:
                baudrate = int(self.custom_baudrate.get_text())
            except ValueError:  # baudrate is empty
                baudrate = 0
        self.serial.baud_rate = baudrate

    @Gtk.Template.Callback()
    def set_data_bits_from_selector(self, selector, *args):
        self.serial.data_bits = int(selector.get_selected_item().get_string())

    @Gtk.Template.Callback()
    def set_stop_bits_from_selector(self, selector, *args):
        self.serial.stop_bits = int(selector.get_selected_item().get_string())

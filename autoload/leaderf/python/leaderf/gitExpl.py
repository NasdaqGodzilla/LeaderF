#!/usr/bin/env python
# -*- coding: utf-8 -*-

import vim
import re
import os
import os.path
import tempfile
import json
from functools import wraps
from .utils import *
from .explorer import *
from .manager import *
from .devicons import (
    webDevIconsGetFileTypeSymbol,
    removeDevIcons,
    matchaddDevIconsDefault,
    matchaddDevIconsExact,
    matchaddDevIconsExtension,
)

#*****************************************************
# GitExplorer
#*****************************************************
class GitExplorer(Explorer):
    def __init__(self):
        self._executor = []
        self._pattern_regex = []
        self._context_separator = "..."
        self._display_multi = False
        self._cmd_work_dir = ""
        self._show_icon = lfEval("get(g:, 'Lf_ShowDevIcons', 1)") == "1"

    def getContent(self, *args, **kwargs):
        arguments_dict = kwargs.get("arguments", {})
        arg_list = arguments_dict.get("arg_line", 'git').split(maxsplit=2)
        if len(arg_list) == 1:
            return

        executor = AsyncExecutor()
        self._executor.append(executor)

        subcommand = arg_list[1]
        if subcommand == "diff":
            cmd = "git diff --name-status"
            if "--cached" in arguments_dict:
                cmd += " --cached"
            if "extra" in arguments_dict:
                cmd += " " + " ".join(arguments_dict["extra"])
            content = executor.execute(cmd, encoding=lfEval("&encoding"), format_line=self.formatLine)
            return content

    def formatLine(self, line):
        """
        R098    README.txt      README.txt.hello
        A       abc.txt
        M       src/fold.c
        """
        name_status = line.split('\t')
        file_name = name_status[1]
        icon = webDevIconsGetFileTypeSymbol(file_name) if self._show_icon else ""
        return "{:<4} {}{}{}".format(name_status[0], icon, name_status[1],
                                     " -> " + name_status[2] if len(name_status) == 3 else "")

    def getStlCategory(self):
        return 'Git'

    def getStlCurDir(self):
        return escQuote(lfEncode(self._cmd_work_dir))

    def supportsNameOnly(self):
        return False

    def cleanup(self):
        for exe in self._executor:
            exe.killProcess()
        self._executor = []

    def getPatternRegex(self):
        return self._pattern_regex

    def getContextSeparator(self):
        return self._context_separator

    def displayMulti(self):
        return self._display_multi


class GitCommand(object):
    def __init__(self, arguments_dict, source=None):
        self._arguments = arguments_dict
        self._source = source
        self._cmd = ""
        self._file_type_cmd = ""
        self._buffer_names = []
        self.buildCommandAndBufferNames()

    def buildCommandAndBufferNames(self):
        pass

    def getCommand(self):
        return self._cmd

    def getFileTypeCommand(self):
        return self._file_type_cmd

    def getBufferNames(self):
        return self._buffer_names

    def getArguments(self):
        return self._arguments

    def getSource(self):
        return self._source


class GitDiffCommand(GitCommand):
    def __init__(self, arguments_dict, source=None):
        super(GitDiffCommand, self).__init__(arguments_dict, source)

    def buildCommandAndBufferNames(self):
        self._cmd = "git diff"
        if "--cached" in self._arguments:
            self._cmd += " --cached"

        if "extra" in self._arguments:
            self._cmd += " " + " ".join(self._arguments["extra"])

        if self._source is not None:
            self._cmd += " -- {}".format(self._source)
        elif ("--current-file" in self._arguments
            and vim.current.buffer.name
            and not vim.current.buffer.options['bt']):
            self._cmd += " -- {}".format(vim.current.buffer.name)

        self._buffer_names.append("LeaderF://" + self._cmd)
        self._file_type_cmd = "silent! doautocmd filetypedetect BufNewFile *.diff"


class GitLogCommand(GitCommand):
    def __init__(self, arguments_dict, source=None):
        super(GitLogCommand, self).__init__(arguments_dict, source)

    def buildCommandAndBufferNames(self):
        if "--directly" in self._arguments:
            self._cmd = "git log"

            if "extra" in self._arguments:
                self._cmd += " " + " ".join(self._arguments["extra"])

            if ("--current-file" in self._arguments
                and vim.current.buffer.name
                and not vim.current.buffer.options['bt']):
                self._cmd += " -- {}".format(vim.current.buffer.name)

            self._buffer_names.append("LeaderF://" + self._cmd)
            self._file_type_cmd = "setlocal filetype=git"


class GitCommandView(object):
    def __init__(self, owner, cmd, buffer_name, window_id):
        self._owner = owner
        self._cmd = cmd
        self._buffer_name = buffer_name
        self._window_id = window_id
        self._executor = AsyncExecutor()
        self._buffer = None
        self.init()
        owner.register(self)

    def init(self):
        self._content = []
        self._reader_thread = None
        self._offset_in_content = 0
        self._read_finished = 0
        self._stop_reader_thread = False

    def getBufferName(self):
        return self._buffer_name

    def getWindowId(self):
        return self._window_id

    def getContent(self):
        return self._content

    def getSource(self):
        return self._cmd.getSource()

    def create(self, bufhidden='wipe'):
        if self._buffer is not None:
            self._buffer.options['modifiable'] = True
            del self._buffer[:]
            self._buffer.options['modifiable'] = False
            self.cleanup()

        self.init()

        lfCmd("call win_execute({}, 'let cur_buf_number = bufnr()')".format(self._window_id))

        lfCmd("call win_execute({}, '{}')".format(self._window_id, self._cmd.getFileTypeCommand()))

        if self._buffer is None:
            lfCmd("call win_execute({}, 'setlocal nobuflisted')".format(self._window_id))
            lfCmd("call win_execute({}, 'setlocal buftype=nofile')".format(self._window_id))
            lfCmd("call win_execute({}, 'setlocal bufhidden={}')".format(self._window_id, bufhidden))
            lfCmd("call win_execute({}, 'setlocal undolevels=-1')".format(self._window_id))
            lfCmd("call win_execute({}, 'setlocal noswapfile')".format(self._window_id))
            lfCmd("call win_execute({}, 'setlocal nospell')".format(self._window_id))
            lfCmd("call win_execute({}, 'setlocal nomodifiable')".format(self._window_id))
            lfCmd("call win_execute({}, 'setlocal nofoldenable')".format(self._window_id))
            if bufhidden == 'wipe':
                lfCmd("call win_execute({}, 'autocmd BufWipeout <buffer> call leaderf#Git#Suicide({})')".format(self._window_id, id(self)))

        self._buffer = vim.buffers[int(lfEval("cur_buf_number"))]

        content = self._executor.execute(self._cmd.getCommand(), encoding=lfEval("&encoding"))

        self._timer_id = lfEval("timer_start(100, function('leaderf#Git#WriteBuffer', [%d]), {'repeat': -1})" % id(self))

        self._reader_thread = threading.Thread(target=self._readContent, args=(content,))
        self._reader_thread.daemon = True
        self._reader_thread.start()

    def writeBuffer(self):
        if self._read_finished == 2:
            return

        if not self._buffer.valid:
            self.stopTimer()
            return

        self._buffer.options['modifiable'] = True
        try:
            cur_len = len(self._content)
            if cur_len > self._offset_in_content:
                if self._offset_in_content == 0:
                    self._buffer[:] = self._content[:cur_len]
                else:
                    self._buffer.append(self._content[self._offset_in_content:cur_len])

                self._offset_in_content = cur_len
        finally:
            self._buffer.options['modifiable'] = False

        if self._read_finished == 1 and self._offset_in_content == len(self._content):
            self._read_finished = 2
            self.stopTimer()

    def _readContent(self, content):
        try:
            for line in content:
                self._content.append(line)
                if self._stop_reader_thread:
                    break
            else:
                self._read_finished = 1
                self._owner.callback(self)
        except Exception as e:
            self._read_finished = 1
            lfPrintError(e)

    def stopThread(self):
        if self._reader_thread and self._reader_thread.is_alive():
            self._stop_reader_thread = True
            self._reader_thread.join()

    def stopTimer(self):
        if self._timer_id is not None:
            lfCmd("call timer_stop(%s)" % self._timer_id)
            self._timer_id = None

    def cleanup(self):
        self._executor.killProcess()
        self.stopTimer()
        self.stopThread()

    def suicide(self):
        self._owner.deregister(self)

    def __del__(self):
        self.cleanup()


class Panel(object):
    def __init__(self):
        pass

    def register(self, view):
        pass

    def deregister(self, view):
        pass

    def cleanup(self):
        pass

    def writeBuffer(self):
        pass

    def callback(self, view):
        pass

class DirectlyPanel(Panel):
    def __init__(self):
        self._views = {}

    def register(self, view):
        self._views[view.getBufferName()] = view

    def deregister(self, view):
        name = view.getBufferName()
        if name in self._views:
            del self._views[name]

    def _createWindow(self, win_pos, buffer_name):
        if win_pos == 'top':
            lfCmd("silent! noa keepa keepj abo sp {}".format(buffer_name))
        elif win_pos == 'bottom':
            lfCmd("silent! noa keepa keepj bel sp {}".format(buffer_name))
        elif win_pos == 'left':
            lfCmd("silent! noa keepa keepj abo vsp {}".format(buffer_name))
        elif win_pos == 'right':
            lfCmd("silent! noa keepa keepj bel vsp {}".format(buffer_name))
        else:
            pass

        return int(lfEval("win_getid()"))

    def create(self, cmd):
        buffer_name = cmd.getBufferNames()[0]
        if buffer_name in self._views:
            self._views[buffer_name].create()
        else:
            winid = self._createWindow(cmd.getArguments().get("--position", ["top"])[0], buffer_name)
            GitCommandView(self, cmd, buffer_name, winid).create()

    def writeBuffer(self):
        for v in self._views.values():
            v.writeBuffer()


class PopupPreviewPanel(Panel):
    def __init__(self):
        self._view = None
        self._buffer_contents = {}
        self._popup_winid = 0

    def register(self, view):
        self._view = view

    def deregister(self, view):
        pass

    def create(self, cmd, config):
        if cmd.getSource() in self._buffer_contents:
            return

        lfCmd("noautocmd silent! let winid = popup_create([], %s)" % json.dumps(config))
        self._popup_winid = int(lfEval("winid"))
        GitCommandView(self, cmd, None, self._popup_winid).create(bufhidden='hide')

    def writeBuffer(self):
        if self._view is not None:
            self._view.writeBuffer()

    def getPopupWinId(self):
        return self._popup_winid

    def cleanup(self):
        self._view = None
        self._buffer_contents = {}

    def callback(self, view):
        self._buffer_contents[view.getSource()] = view.getContent()

    def getBufferContents(self):
        return self._buffer_contents



#*****************************************************
# GitExplManager
#*****************************************************
class GitExplManager(Manager):
    def __init__(self):
        super(GitExplManager, self).__init__()
        self._subcommand = ""
        self._directly_panel = DirectlyPanel()
        self._popup_preview_panel = PopupPreviewPanel()

    def _getExplClass(self):
        return GitExplorer

    def _defineMaps(self):
        lfCmd("call leaderf#Git#Maps()")

    def _workInIdle(self, content=None, bang=False):
        self._directly_panel.writeBuffer()
        self._popup_preview_panel.writeBuffer()

        super(GitExplManager, self)._workInIdle(content, bang)

    def _afterEnter(self):
        super(GitExplManager, self)._afterEnter()

        if lfEval("get(g:, 'Lf_ShowDevIcons', 1)") == '1':
            winid = self._getInstance().getPopupWinId() if self._getInstance().getWinPos() == 'popup' else None
            icon_pattern = r'^\S*\s*\zs__icon__'
            self._match_ids.extend(matchaddDevIconsExtension(icon_pattern, winid))
            self._match_ids.extend(matchaddDevIconsExact(icon_pattern, winid))
            self._match_ids.extend(matchaddDevIconsDefault(icon_pattern, winid))

    def _beforeExit(self):
        super(GitExplManager, self)._beforeExit()
        self._popup_preview_panel.cleanup()

    def startGitDiff(self, win_pos, *args, **kwargs):
        if "--directly" in self._arguments:
            self._directly_panel.create(GitDiffCommand(self._arguments))
        elif "--explorer" in self._arguments:
            pass
        else:
            super(GitExplManager, self).startExplorer(win_pos, *args, **kwargs)

    def startGitLog(self, win_pos, *args, **kwargs):
        if "--directly" in self._arguments:
            self._directly_panel.create(GitLogCommand(self._arguments))

    def startGitBlame(self, win_pos, *args, **kwargs):
        pass

    def startExplorer(self, win_pos, *args, **kwargs):
        arguments_dict = kwargs.get("arguments", {})
        self.setArguments(arguments_dict)
        arg_list = arguments_dict.get("arg_line", 'git').split(maxsplit=2)
        if len(arg_list) == 1:
            return

        self._subcommand = arg_list[1]
        if self._subcommand == "diff":
            self.startGitDiff(win_pos, *args, **kwargs)
        elif self._subcommand == "log":
            self.startGitLog(win_pos, *args, **kwargs)
        elif self._subcommand == "blame":
            self.startGitBlame(win_pos, *args, **kwargs)

    def _bangEnter(self):
        super(GitExplManager, self)._bangEnter()

        if lfEval("exists('*timer_start')") == '0':
            lfCmd("echohl Error | redraw | echo ' E117: Unknown function: timer_start' | echohl NONE")
            return

        self._callback(bang=True)
        if self._read_finished < 2:
            self._timer_id = lfEval("timer_start(10, 'leaderf#Git#TimerCallback', {'repeat': -1})")

    def _getFineName(self, line):
        return line.split()[-1]

    def _previewInPopup(self, *args, **kwargs):
        if len(args) == 0 or args[0] == '':
            return

        line = args[0]
        filename = self._getFineName(line)

        self._createPopupPreview("", filename, 0)

    def _createPreviewWindow(self, config, source, line_num, jump_cmd):
        self._preview_config = config
        filename = source

        if lfEval("has('nvim')") == '1':
            lfCmd("noautocmd let g:Lf_preview_scratch_buffer = nvim_create_buf(0, 1)")
            self._preview_winid = int(lfEval("nvim_open_win(g:Lf_preview_scratch_buffer, 0, %s)" % str(config)))
            diff_view = GitCommandView(self, "git diff", "diff", 'aa', self._preview_winid)
            diff_view.create()

            # cur_winid = lfEval("win_getid()")
            # lfCmd("noautocmd call win_gotoid(%d)" % self._preview_winid)
            # if not isinstance(source, int):
            #     lfCmd("silent! doautocmd filetypedetect BufNewFile %s" % source)
            # lfCmd("noautocmd call win_gotoid(%s)" % cur_winid)

            self._setWinOptions(self._preview_winid)
            self._preview_filetype = lfEval("getbufvar(winbufnr(%d), '&ft')" % self._preview_winid)
        else:
            if self._subcommand == "diff":
                self._popup_preview_panel.create(GitDiffCommand(self._arguments, source), config)
                self._preview_winid = self._popup_preview_panel.getPopupWinId()

            self._setWinOptions(self._preview_winid)
            # self._preview_filetype = lfEval("getbufvar(winbufnr(winid), '&ft')")

    def _useExistingWindow(self, title, source, line_num, jump_cmd):
        self.setOptionsForCursor()

        if lfEval("has('nvim')") == '1':
            if isinstance(source, int):
                lfCmd("noautocmd call nvim_win_set_buf(%d, %d)" % (self._preview_winid, source))
                self._setWinOptions(self._preview_winid)
                self._preview_filetype = ''
            else:
                try:
                    if self._isBinaryFile(source):
                        lfCmd("""let content = map(range(128), '"^@"')""")
                    else:
                        lfCmd("let content = readfile('%s', '', 4096)" % escQuote(source))
                except vim.error as e:
                    lfPrintError(e)
                    return
                if lfEval("!exists('g:Lf_preview_scratch_buffer') || !bufexists(g:Lf_preview_scratch_buffer)") == '1':
                    lfCmd("noautocmd let g:Lf_preview_scratch_buffer = nvim_create_buf(0, 1)")
                lfCmd("noautocmd call nvim_buf_set_option(g:Lf_preview_scratch_buffer, 'undolevels', -1)")
                lfCmd("noautocmd call nvim_buf_set_option(g:Lf_preview_scratch_buffer, 'modeline', v:true)")
                lfCmd("noautocmd call nvim_buf_set_lines(g:Lf_preview_scratch_buffer, 0, -1, v:false, content)")
                lfCmd("noautocmd call nvim_win_set_buf(%d, g:Lf_preview_scratch_buffer)" % self._preview_winid)

                cur_filetype = getExtension(source)
                if cur_filetype != self._preview_filetype:
                    lfCmd("call win_execute(%d, 'silent! doautocmd filetypedetect BufNewFile %s')" % (self._preview_winid, escQuote(source)))
                    self._preview_filetype = lfEval("getbufvar(winbufnr(%d), '&ft')" % self._preview_winid)
        else:
            content = self._popup_preview_panel.getBufferContents().get(source, None)
            if content is not None:
                lfCmd("noautocmd call popup_settext({}, {})".format(self._preview_winid, content))
            else:
                filename = source
                try:
                    if self._isBinaryFile(filename):
                        lfCmd("""let content = map(range(128), '"^@"')""")
                    else:
                        lfCmd("let content = readfile('%s', '', 4096)" % escQuote(filename))
                except vim.error as e:
                    lfPrintError(e)
                    return
                lfCmd("noautocmd call popup_settext(%d, content)" % self._preview_winid )

            cur_filetype = getExtension(filename)
            if cur_filetype != self._preview_filetype:
                lfCmd("call win_execute(%d, 'silent! doautocmd filetypedetect BufNewFile %s')" % (self._preview_winid, escQuote(filename)))
                self._preview_filetype = lfEval("getbufvar(winbufnr(%d), '&ft')" % self._preview_winid)

        # self._setWinOptions(self._preview_winid)

        # if jump_cmd:
        #     lfCmd("""call win_execute(%d, '%s')""" % (self._preview_winid, escQuote(jump_cmd)))
        #     lfCmd("call win_execute(%d, 'norm! zz')" % self._preview_winid)
        # elif line_num > 0:
        #     lfCmd("""call win_execute(%d, "call cursor(%d, 1)")""" % (self._preview_winid, line_num))
        #     lfCmd("call win_execute(%d, 'norm! zz')" % self._preview_winid)
        # else:
        #     lfCmd("call win_execute(%d, 'norm! gg')" % self._preview_winid)


#*****************************************************
# gitExplManager is a singleton
#*****************************************************
gitExplManager = GitExplManager()

__all__ = ['gitExplManager']
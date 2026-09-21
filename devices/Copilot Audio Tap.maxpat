{
	"patcher" : 	{
		"fileversion" : 1,
		"appversion" : 		{
			"major" : 9,
			"minor" : 1,
			"revision" : 5,
			"architecture" : "x64",
			"modernui" : 1
		}
,
		"classnamespace" : "box",
		"rect" : [ 40.0, 80.0, 520.0, 360.0 ],
		"bglocked" : 0,
		"openinpresentation" : 1,
		"default_fontsize" : 12.0,
		"default_fontface" : 0,
		"default_fontname" : "Arial",
		"gridonopen" : 1,
		"gridsize" : [ 15.0, 15.0 ],
		"gridsnaponopen" : 1,
		"objectsnaponopen" : 1,
		"statusbarvisible" : 2,
		"toolbarvisible" : 1,
		"lefttoolbarpinned" : 0,
		"toptoolbarpinned" : 0,
		"righttoolbarpinned" : 0,
		"bottomtoolbarpinned" : 0,
		"toolbars_unpinned_last_save" : 0,
		"tallnewobj" : 0,
		"boxanimatetime" : 200,
		"enablehscroll" : 1,
		"enablevscroll" : 1,
		"devicewidth" : 165.0,
		"description" : "Transparent stereo Master tap. Pass-through audio, record to WAV.",
		"digest" : "Copilot Audio Tap",
		"tags" : "copilot",
		"style" : "",
		"subpatcher_template" : "",
		"assistshowspatchername" : 0,
		"boxes" : [ 			{
				"box" : 				{
					"id" : "obj-title",
					"maxclass" : "comment",
					"numinlets" : 1,
					"numoutlets" : 0,
					"patching_rect" : [ 12.0, 8.0, 140.0, 20.0 ],
					"presentation" : 1,
					"presentation_rect" : [ 8.0, 6.0, 150.0, 20.0 ],
					"text" : "Copilot Audio Tap"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-reclabel",
					"maxclass" : "comment",
					"numinlets" : 1,
					"numoutlets" : 0,
					"patching_rect" : [ 36.0, 40.0, 30.0, 20.0 ],
					"presentation" : 1,
					"presentation_rect" : [ 32.0, 32.0, 30.0, 20.0 ],
					"text" : "Rec"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-this",
					"maxclass" : "newobj",
					"numinlets" : 2,
					"numoutlets" : 2,
					"outlettype" : [ "", "" ],
					"patching_rect" : [ 360.0, 12.0, 84.0, 22.0 ],
					"text" : "live.thisdevice"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-plugin",
					"maxclass" : "newobj",
					"numinlets" : 1,
					"numoutlets" : 2,
					"outlettype" : [ "signal", "signal" ],
					"patching_rect" : [ 12.0, 200.0, 67.0, 22.0 ],
					"text" : "plugin~ 2"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-plugout",
					"maxclass" : "newobj",
					"numinlets" : 2,
					"numoutlets" : 0,
					"patching_rect" : [ 12.0, 280.0, 73.0, 22.0 ],
					"text" : "plugout~ 2"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-rec",
					"maxclass" : "newobj",
					"numinlets" : 2,
					"numoutlets" : 1,
					"outlettype" : [ "signal" ],
					"patching_rect" : [ 160.0, 280.0, 84.0, 22.0 ],
					"text" : "sfrecord~ 2"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-tog",
					"maxclass" : "live.toggle",
					"numinlets" : 1,
					"numoutlets" : 1,
					"outlettype" : [ "" ],
					"parameter_enable" : 1,
					"patching_rect" : [ 12.0, 40.0, 18.0, 18.0 ],
					"presentation" : 1,
					"presentation_rect" : [ 10.0, 32.0, 18.0, 18.0 ],
					"saved_attribute_attributes" : 					{
						"valueof" : 						{
							"parameter_initial" : [ 0 ],
							"parameter_initial_enable" : 1,
							"parameter_longname" : "Rec",
							"parameter_mmax" : 1.0,
							"parameter_mmin" : 0.0,
							"parameter_shortname" : "Rec",
							"parameter_type" : 2,
							"parameter_unitstyle" : 9,
							"parameter_enum" : [ "off", "on" ]
						}

					}
,
					"varname" : "Rec"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-udp",
					"maxclass" : "newobj",
					"numinlets" : 1,
					"numoutlets" : 1,
					"outlettype" : [ "" ],
					"patching_rect" : [ 200.0, 40.0, 111.0, 22.0 ],
					"text" : "udpreceive 19877"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-sel",
					"maxclass" : "newobj",
					"numinlets" : 2,
					"numoutlets" : 3,
					"outlettype" : [ "bang", "bang", "" ],
					"patching_rect" : [ 12.0, 80.0, 47.0, 22.0 ],
					"text" : "sel 1 0"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-trig",
					"maxclass" : "newobj",
					"numinlets" : 1,
					"numoutlets" : 2,
					"outlettype" : [ "bang", "bang" ],
					"patching_rect" : [ 12.0, 112.0, 39.0, 22.0 ],
					"text" : "t b b"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-open",
					"maxclass" : "message",
					"numinlets" : 2,
					"numoutlets" : 1,
					"outlettype" : [ "" ],
					"patching_rect" : [ 56.0, 144.0, 250.0, 22.0 ],
					"text" : "open /Users/lucas/Music/MusicCopilot/captures/_next.wav"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-delay",
					"maxclass" : "newobj",
					"numinlets" : 2,
					"numoutlets" : 1,
					"outlettype" : [ "bang" ],
					"patching_rect" : [ 12.0, 144.0, 56.0, 22.0 ],
					"text" : "delay 50"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-one",
					"maxclass" : "message",
					"numinlets" : 2,
					"numoutlets" : 1,
					"outlettype" : [ "" ],
					"patching_rect" : [ 12.0, 176.0, 29.5, 22.0 ],
					"text" : "1"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-zero",
					"maxclass" : "message",
					"numinlets" : 2,
					"numoutlets" : 1,
					"outlettype" : [ "" ],
					"patching_rect" : [ 80.0, 112.0, 52.0, 22.0 ],
					"text" : "0, close"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-lb",
					"maxclass" : "newobj",
					"numinlets" : 1,
					"numoutlets" : 1,
					"outlettype" : [ "bang" ],
					"patching_rect" : [ 280.0, 80.0, 59.0, 22.0 ],
					"text" : "loadbang"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-init0",
					"maxclass" : "message",
					"numinlets" : 2,
					"numoutlets" : 1,
					"outlettype" : [ "" ],
					"patching_rect" : [ 280.0, 112.0, 29.5, 22.0 ],
					"text" : "0"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-samp",
					"maxclass" : "message",
					"numinlets" : 2,
					"numoutlets" : 1,
					"outlettype" : [ "" ],
					"patching_rect" : [ 320.0, 144.0, 99.0, 22.0 ],
					"text" : "samptype float32"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-wave",
					"maxclass" : "message",
					"numinlets" : 2,
					"numoutlets" : 1,
					"outlettype" : [ "" ],
					"patching_rect" : [ 320.0, 176.0, 82.0, 22.0 ],
					"text" : "filetype wave"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-slot",
					"maxclass" : "live.numbox",
					"numinlets" : 2,
					"numoutlets" : 2,
					"outlettype" : [ "", "float" ],
					"parameter_enable" : 1,
					"patching_rect" : [ 200.0, 72.0, 40.0, 15.0 ],
					"presentation" : 1,
					"presentation_rect" : [ 80.0, 32.0, 36.0, 15.0 ],
					"saved_attribute_attributes" : 					{
						"valueof" : 						{
							"parameter_initial" : [ 0 ],
							"parameter_initial_enable" : 1,
							"parameter_longname" : "Slot",
							"parameter_mmax" : 2.0,
							"parameter_mmin" : 0.0,
							"parameter_shortname" : "Slot",
							"parameter_type" : 1,
							"parameter_unitstyle" : 1
						}

					}
,
					"varname" : "Slot"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-proto",
					"maxclass" : "live.numbox",
					"numinlets" : 2,
					"numoutlets" : 2,
					"outlettype" : [ "", "float" ],
					"parameter_enable" : 1,
					"patching_rect" : [ 250.0, 72.0, 40.0, 15.0 ],
					"presentation" : 1,
					"presentation_rect" : [ 124.0, 32.0, 36.0, 15.0 ],
					"saved_attribute_attributes" : 					{
						"valueof" : 						{
							"parameter_initial" : [ 3 ],
							"parameter_initial_enable" : 1,
							"parameter_longname" : "TapProtocol",
							"parameter_mmax" : 16.0,
							"parameter_mmin" : 1.0,
							"parameter_shortname" : "TapProtocol",
							"parameter_type" : 1,
							"parameter_unitstyle" : 1
						}

					}
,
					"varname" : "TapProtocol"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-toi",
					"maxclass" : "newobj",
					"numinlets" : 1,
					"numoutlets" : 1,
					"outlettype" : [ "int" ],
					"patching_rect" : [ 200.0, 92.0, 19.0, 22.0 ],
					"text" : "i"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-slotsel",
					"maxclass" : "newobj",
					"numinlets" : 3,
					"numoutlets" : 4,
					"outlettype" : [ "bang", "bang", "bang", "" ],
					"patching_rect" : [ 200.0, 112.0, 73.0, 22.0 ],
					"text" : "sel 0 1 2"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-openk",
					"maxclass" : "message",
					"numinlets" : 2,
					"numoutlets" : 1,
					"outlettype" : [ "" ],
					"patching_rect" : [ 56.0, 168.0, 270.0, 22.0 ],
					"text" : "open /Users/lucas/Music/MusicCopilot/captures/_next_kick.wav"
				}

			}
, 			{
				"box" : 				{
					"id" : "obj-openb",
					"maxclass" : "message",
					"numinlets" : 2,
					"numoutlets" : 1,
					"outlettype" : [ "" ],
					"patching_rect" : [ 56.0, 192.0, 270.0, 22.0 ],
					"text" : "open /Users/lucas/Music/MusicCopilot/captures/_next_bass.wav"
				}

			}
 ],
		"lines" : [ 			{
				"patchline" : 				{
					"destination" : [ "obj-plugout", 0 ],
					"source" : [ "obj-plugin", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-plugout", 1 ],
					"source" : [ "obj-plugin", 1 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-rec", 0 ],
					"source" : [ "obj-plugin", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-rec", 1 ],
					"source" : [ "obj-plugin", 1 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-sel", 0 ],
					"source" : [ "obj-tog", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-trig", 0 ],
					"source" : [ "obj-sel", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-zero", 0 ],
					"source" : [ "obj-sel", 1 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-delay", 0 ],
					"source" : [ "obj-trig", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-slot", 0 ],
					"source" : [ "obj-trig", 1 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-toi", 0 ],
					"source" : [ "obj-slot", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-slotsel", 0 ],
					"source" : [ "obj-toi", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-open", 0 ],
					"source" : [ "obj-slotsel", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-openk", 0 ],
					"source" : [ "obj-slotsel", 1 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-openb", 0 ],
					"source" : [ "obj-slotsel", 2 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-rec", 0 ],
					"source" : [ "obj-openk", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-rec", 0 ],
					"source" : [ "obj-openb", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-one", 0 ],
					"source" : [ "obj-delay", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-rec", 0 ],
					"source" : [ "obj-open", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-rec", 0 ],
					"source" : [ "obj-one", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-rec", 0 ],
					"source" : [ "obj-zero", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-init0", 0 ],
					"source" : [ "obj-lb", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-samp", 0 ],
					"source" : [ "obj-lb", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-wave", 0 ],
					"source" : [ "obj-lb", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-tog", 0 ],
					"source" : [ "obj-init0", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-rec", 0 ],
					"source" : [ "obj-samp", 0 ]
				}

			}
, 			{
				"patchline" : 				{
					"destination" : [ "obj-rec", 0 ],
					"source" : [ "obj-wave", 0 ]
				}

			}
 ]
	}

}

"""Editable phase instructions; output schemas remain in the execution pipeline."""
EXTRA_PROMPTS = {'conversation_locations': ('Discover locations',
                            'Which physical places appear in this scene summary? Briefly describe each '
                            'place using only the information given.'),
 'conversation_objects': ('Discover important objects',
                          'Identify physical objects important to the plot or recurring in this passage. '
                          'Give each distinct object a stable, specific name and briefly describe stated '
                          'shape, material and color. Exclude people, places, clothing and incidental '
                          'props. Do not invent objects or details. If none, say none.'),
 'location_additions': ('Merge new locations',
                        'Compare the location summaries below. Return ONLY places in SECOND TEXT absent '
                        'from FIRST TEXT. Match physical identity, not wording: the drawing room, salon '
                        'and living room of the same house may be the same place. Changes in lighting, '
                        'time, decoration, condition or occupants do not create another location. Do not '
                        'merge distinct rooms, different buildings or similar places at different '
                        'addresses. Keep the first canonical name for known locations and do not repeat '
                        'them. For each new location give its specific name and stated visual description. '
                        'Do not invent details. If there are no new locations, return exactly NONE. No '
                        'commentary.'),
 'object_additions': ('Merge new objects',
                      'Compare the object summaries below. Return ONLY physical objects in SECOND TEXT '
                      'that are absent from FIRST TEXT. Match identity, not wording: a portrait, painting '
                      'or canvas of the same person can be the same object. Changes in appearance, '
                      'condition, location or owner do not create another object. Do not merge genuinely '
                      'distinct objects, copies or portraits of different people. Keep the first canonical '
                      'name for known objects and do not repeat them. For each new object give its '
                      'specific name and stated visual description. Do not invent details. If there are no '
                      'new objects, return exactly NONE. No commentary.'),
 'era_structure': ('Structure periods and source anchors',
                   'Convert the reviewed period report into chronological era_events. Each event starts at '
                   'an exact source sentence, copied into start_quote. Describe the period actually '
                   'illustrated, not dates merely mentioned. Reuse known_id for the same period, even on a '
                   'return or flashback; otherwise leave it empty. name is a short English historical '
                   'period name only (e.g. Neolithic, Bronze Age, Victorian era, 1920s), without regions, '
                   'locations, environments or narrative summaries. Set description equal to name. '
                   'visual_context separately lists only period-specific clothing, architecture and '
                   'technology, without geography or scenery. For present-day events set name and '
                   'description to Present day, is_current to true and visual_context empty; keep '
                   'transitions to the present so a previous historical period stops applying. Otherwise '
                   'is_current is false. evidence quotes the source; basis is explicit, inferred or '
                   'unknown. For an unknown setting leave name, description, visual_context and known_id '
                   'empty. Preserve reviewed corrections. Do not invent source quotes, timestamps, precise '
                   'years or additional events.'),
 'objects_profiles': ('Build objects visual profiles',
                      'Convert this object summary to JSON: object and visual_description. Keep canonical '
                      'names and stated visual facts; merge repeated mentions of the same object. Exclude '
                      'incidental props. Return an empty objects list if none. Do not invent details.'),
 'characters_profiles': ('Build characters visual profiles',
                         'Convert the summary to JSON, one entry per named person; exclude groups. '
                         '"character" is their name, never their species. "visual_description" must '
                         'describe species, approximate age, hair length and color, and clothing with a '
                         'distinct color. Preserve stated traits; invent missing hair and clothing as a '
                         'consistent storyboard design. Write neutral portraits in 15-25 words, without '
                         'actions, relationships, burial details or props.'),
 'locations_profiles': ('Build locations visual profiles',
                        'Convert this place summary to JSON: location and visual_description for a '
                        'storyboard. Keep the names and visual facts. Be concise; do not invent details, '
                        'events or changes.'),
 'scene_visuals': ('Write scene image prompts',
                   'Use concrete English visual descriptions of 25-55 words. Use canonical names from '
                   'profiles but do not repeat their appearance. List only visible characters and '
                   'locations by canonical name. Resolve pronouns using story context, not by inserting '
                   'everyone. No style or zoom/motion instructions. For repeated action vary framing or '
                   'show an important existing detail; never anticipate later actions.'),
 'scene_structure': ('Structure scene proposals',
                     'Convert these proposed scenes to JSON, in the same order. Copy each title and start '
                     'sentence exactly. Copy each fragment-scene label (e.g. 2-3) into source_proposal_id; '
                     'use an empty string if absent. Do not invent or rewrite scenes or quotes.'),
 'character_portraits': ('Update changed portraits',
                         'Update this storyboard portrait using ONLY the explicit visual changes supplied. '
                         'Keep all unaffected traits exactly, including human hair length/color or '
                         'baldness. Replace superseded traits; do not concatenate conflicting outfits or '
                         'ages. Return one concise complete visual description, no actions, emotions or '
                         'alternatives.'),
 'character_changes': ('Find explicit appearance changes',
                       "Find only actual changes to a named character's clothing, age, hair or lasting "
                       'physical appearance. The summary is guidance; verify each change in the original '
                       'passage. No moods, actions, possessions, newly revealed unchanged traits, '
                       'hypothetical changes or initial descriptions. Copy a unique sentence from the '
                       'passage where the changed appearance starts. Describe only the changed visual '
                       'traits. Use supplied character names. Return changes: [] if none.')}

#version 330 core
// Masked mode fragment shader: luminance-preserving palette-slot recolouring (ARCHITECTURE.md §14.6.1).

uniform sampler2D u_diffuse_tex;
uniform sampler2D u_mask_tex;
uniform sampler2D u_base_palette;
uniform sampler2D u_live_palette;
uniform vec3 u_fade_tint;
uniform vec4 u_flash_mix;

in vec2 v_uv;
out vec4 f_color;

void main() {
    vec4 d = texture(u_diffuse_tex, v_uv);
    int slot = int(texture(u_mask_tex, v_uv).r * 255.0 + 0.5);
    if (slot <= 0 || slot > 31) {
        vec3 faded = d.rgb * u_fade_tint;
        vec3 rgb = clamp(mix(faded, u_flash_mix.rgb, u_flash_mix.a), 0.0, 1.0);
        f_color = vec4(rgb, d.a);
        return;
    }
    vec3 base_col = texelFetch(u_base_palette, ivec2(slot, 0), 0).rgb;
    vec3 live_col = texelFetch(u_live_palette, ivec2(slot, 0), 0).rgb;
    float y_d = dot(d.rgb, vec3(0.2126, 0.7152, 0.0722));
    float y_b = dot(base_col, vec3(0.2126, 0.7152, 0.0722));
    float shade = (y_b > 0.001) ? clamp(y_d / y_b, 0.0, 2.0) : 1.0;
    vec3 rgb = clamp(live_col * shade, 0.0, 1.0);
    f_color = vec4(rgb, d.a);
}

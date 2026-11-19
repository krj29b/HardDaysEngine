#version 330 core
// Clean-room CRT bloom horizontal pass: bright extraction + 9-tap Gaussian blur (§13.4.1).

uniform sampler2D u_input_tex;
uniform vec2 u_in_origin;
uniform vec2 u_in_size;
uniform vec2 u_in_tex_size;

in vec2 v_uv;
out vec4 f_color;

vec3 sample_bright_linear(vec2 px) {
    vec2 clamped = clamp(px, vec2(0.5), u_in_size - vec2(0.5));
    vec2 uv = vec2(
        (u_in_origin.x + clamped.x) / u_in_tex_size.x,
        1.0 - (u_in_origin.y + clamped.y) / u_in_tex_size.y
    );
    vec3 srgb = texture(u_input_tex, uv).rgb;
    vec3 lin = pow(max(srgb, vec3(0.0)), vec3(2.2));
    float luma = dot(lin, vec3(0.2126, 0.7152, 0.0722));
    float knee = smoothstep(0.12, 0.65, luma);
    return lin * knee;
}

void main() {
    vec2 base_px = vec2(v_uv.x, 1.0 - v_uv.y) * u_in_size;
    const float W0 = 0.204164;
    const float W1 = 0.180174;
    const float W2 = 0.123832;
    const float W3 = 0.066282;
    const float W4 = 0.027631;

    vec3 acc = sample_bright_linear(base_px) * W0;
    acc += (sample_bright_linear(base_px + vec2(1.0, 0.0)) + sample_bright_linear(base_px - vec2(1.0, 0.0))) * W1;
    acc += (sample_bright_linear(base_px + vec2(2.0, 0.0)) + sample_bright_linear(base_px - vec2(2.0, 0.0))) * W2;
    acc += (sample_bright_linear(base_px + vec2(3.0, 0.0)) + sample_bright_linear(base_px - vec2(3.0, 0.0))) * W3;
    acc += (sample_bright_linear(base_px + vec2(4.0, 0.0)) + sample_bright_linear(base_px - vec2(4.0, 0.0))) * W4;

    f_color = vec4(sqrt(clamp(acc, vec3(0.0), vec3(1.0))), 1.0);
}
